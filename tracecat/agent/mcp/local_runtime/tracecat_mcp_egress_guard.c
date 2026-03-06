#define _GNU_SOURCE

#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <netinet/in.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>

#define ALLOW_ENV "TRACECAT_MCP_EGRESS_ALLOW_CIDRS"
#define DENY_ENV "TRACECAT_MCP_EGRESS_DENY_CIDRS"

typedef struct {
  sa_family_t family;
  uint8_t prefix_len;
  uint8_t addr[16];
} cidr_rule_t;

typedef struct {
  cidr_rule_t *items;
  size_t count;
} cidr_rule_set_t;

static pthread_once_t g_policy_once = PTHREAD_ONCE_INIT;
static cidr_rule_set_t g_allow_rules = {0};
static cidr_rule_set_t g_deny_rules = {0};

static int (*real_connect)(int, const struct sockaddr *, socklen_t) = NULL;
static ssize_t (*real_sendto)(int, const void *, size_t, int, const struct sockaddr *,
                              socklen_t) = NULL;

static bool parse_cidr_token(const char *token, cidr_rule_t *rule) {
  if (token == NULL || *token == '\0') {
    return false;
  }

  char buffer[INET6_ADDRSTRLEN + 4];
  if (strlen(token) >= sizeof(buffer)) {
    return false;
  }
  strcpy(buffer, token);

  char *slash = strchr(buffer, '/');
  char *addr_text = buffer;
  int prefix_len = -1;
  if (slash != NULL) {
    *slash = '\0';
    prefix_len = atoi(slash + 1);
  }

  if (inet_pton(AF_INET, addr_text, rule->addr) == 1) {
    rule->family = AF_INET;
    rule->prefix_len = (uint8_t)(prefix_len >= 0 ? prefix_len : 32);
    return rule->prefix_len <= 32;
  }
  if (inet_pton(AF_INET6, addr_text, rule->addr) == 1) {
    rule->family = AF_INET6;
    rule->prefix_len = (uint8_t)(prefix_len >= 0 ? prefix_len : 128);
    return rule->prefix_len <= 128;
  }
  return false;
}

static void load_rule_set(const char *env_name, cidr_rule_set_t *target) {
  const char *env_value = getenv(env_name);
  if (env_value == NULL || *env_value == '\0') {
    return;
  }

  char *raw = strdup(env_value);
  if (raw == NULL) {
    return;
  }

  size_t capacity = 4;
  cidr_rule_t *rules = calloc(capacity, sizeof(cidr_rule_t));
  if (rules == NULL) {
    free(raw);
    return;
  }

  size_t count = 0;
  char *saveptr = NULL;
  for (char *token = strtok_r(raw, ",", &saveptr); token != NULL;
       token = strtok_r(NULL, ",", &saveptr)) {
    cidr_rule_t rule = {0};
    if (!parse_cidr_token(token, &rule)) {
      continue;
    }
    if (count == capacity) {
      capacity *= 2;
      cidr_rule_t *resized = realloc(rules, capacity * sizeof(cidr_rule_t));
      if (resized == NULL) {
        break;
      }
      rules = resized;
    }
    rules[count++] = rule;
  }

  free(raw);
  target->items = rules;
  target->count = count;
}

static void init_policy(void) {
  real_connect = dlsym(RTLD_NEXT, "connect");
  real_sendto = dlsym(RTLD_NEXT, "sendto");
  load_rule_set(ALLOW_ENV, &g_allow_rules);
  load_rule_set(DENY_ENV, &g_deny_rules);
}

static bool addr_matches_rule(const uint8_t *addr, const cidr_rule_t *rule) {
  size_t full_bytes = rule->prefix_len / 8;
  uint8_t remainder_bits = rule->prefix_len % 8;

  if (full_bytes > 0 && memcmp(addr, rule->addr, full_bytes) != 0) {
    return false;
  }
  if (remainder_bits == 0) {
    return true;
  }

  uint8_t mask = (uint8_t)(0xFFu << (8 - remainder_bits));
  return (addr[full_bytes] & mask) == (rule->addr[full_bytes] & mask);
}

static bool rule_set_matches(const uint8_t *addr, sa_family_t family,
                             const cidr_rule_set_t *rules) {
  for (size_t i = 0; i < rules->count; i++) {
    if (rules->items[i].family != family) {
      continue;
    }
    if (addr_matches_rule(addr, &rules->items[i])) {
      return true;
    }
  }
  return false;
}

static bool should_block_destination(const struct sockaddr *addr) {
  if (addr == NULL) {
    return false;
  }

  pthread_once(&g_policy_once, init_policy);

  const uint8_t *raw_addr = NULL;
  sa_family_t family = addr->sa_family;
  uint8_t address_buffer[16] = {0};

  switch (family) {
  case AF_INET: {
    const struct sockaddr_in *addr4 = (const struct sockaddr_in *)addr;
    memcpy(address_buffer, &addr4->sin_addr, 4);
    raw_addr = address_buffer;
    break;
  }
  case AF_INET6: {
    const struct sockaddr_in6 *addr6 = (const struct sockaddr_in6 *)addr;
    memcpy(address_buffer, &addr6->sin6_addr, 16);
    raw_addr = address_buffer;
    break;
  }
  default:
    return false;
  }

  if (rule_set_matches(raw_addr, family, &g_deny_rules)) {
    return true;
  }
  if (g_allow_rules.count > 0 && !rule_set_matches(raw_addr, family, &g_allow_rules)) {
    return true;
  }
  return false;
}

int connect(int sockfd, const struct sockaddr *addr, socklen_t addrlen) {
  pthread_once(&g_policy_once, init_policy);
  if (real_connect == NULL) {
    errno = ENOSYS;
    return -1;
  }
  if (should_block_destination(addr)) {
    errno = EACCES;
    return -1;
  }
  return real_connect(sockfd, addr, addrlen);
}

ssize_t sendto(int sockfd, const void *buf, size_t len, int flags,
               const struct sockaddr *dest_addr, socklen_t addrlen) {
  pthread_once(&g_policy_once, init_policy);
  if (real_sendto == NULL) {
    errno = ENOSYS;
    return -1;
  }
  if (should_block_destination(dest_addr)) {
    errno = EACCES;
    return -1;
  }
  return real_sendto(sockfd, buf, len, flags, dest_addr, addrlen);
}
