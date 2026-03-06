#define _GNU_SOURCE

#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <netdb.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>

#define MAX_RULES 128
#define MAX_ENTRY_LEN 256

typedef struct {
  char host[MAX_ENTRY_LEN];
  char port[MAX_ENTRY_LEN];
  bool has_port;
} egress_rule_t;

typedef struct {
  egress_rule_t rules[MAX_RULES];
  size_t count;
} egress_rule_list_t;

static pthread_once_t guard_init_once = PTHREAD_ONCE_INIT;
static egress_rule_list_t allow_rules = {0};
static egress_rule_list_t deny_rules = {0};
static bool has_allow_rules = false;

static int (*real_connect)(int, const struct sockaddr *, socklen_t) = NULL;
static int (*real_getaddrinfo)(const char *, const char *, const struct addrinfo *,
                               struct addrinfo **) = NULL;

static void trim_copy(char *dst, size_t dst_size, const char *src, size_t src_len) {
  while (src_len > 0 && (*src == ' ' || *src == '"' || *src == '\'')) {
    src++;
    src_len--;
  }
  while (src_len > 0 &&
         (src[src_len - 1] == ' ' || src[src_len - 1] == '"' || src[src_len - 1] == '\'')) {
    src_len--;
  }
  if (src_len >= dst_size) {
    src_len = dst_size - 1;
  }
  memcpy(dst, src, src_len);
  dst[src_len] = '\0';
}

static const char *find_last_colon(const char *token, size_t token_len) {
  for (size_t i = token_len; i > 0; --i) {
    if (token[i - 1] == ':') {
      return token + (i - 1);
    }
  }
  return NULL;
}

static void parse_rule_token(const char *token, size_t token_len, egress_rule_t *rule) {
  memset(rule, 0, sizeof(*rule));
  const char *colon = find_last_colon(token, token_len);
  if (colon != NULL && memchr(token, ']', token_len) == NULL && memchr(token, ':', token_len) == colon) {
    trim_copy(rule->host, sizeof(rule->host), token, (size_t)(colon - token));
    trim_copy(rule->port, sizeof(rule->port), colon + 1, token_len - (size_t)(colon - token) - 1);
    rule->has_port = rule->port[0] != '\0';
    return;
  }
  trim_copy(rule->host, sizeof(rule->host), token, token_len);
}

static void parse_rule_env(const char *raw, egress_rule_list_t *out) {
  if (raw == NULL) {
    return;
  }
  const char *cursor = raw;
  while (*cursor != '\0' && out->count < MAX_RULES) {
    while (*cursor != '\0' && *cursor != '"') {
      cursor++;
    }
    if (*cursor == '\0') {
      break;
    }
    cursor++;
    const char *start = cursor;
    while (*cursor != '\0' && *cursor != '"') {
      cursor++;
    }
    if (*cursor == '"') {
      parse_rule_token(start, (size_t)(cursor - start), &out->rules[out->count]);
      if (out->rules[out->count].host[0] != '\0') {
        out->count++;
      }
      cursor++;
    }
  }
}

static void init_guard(void) {
  real_connect = dlsym(RTLD_NEXT, "connect");
  real_getaddrinfo = dlsym(RTLD_NEXT, "getaddrinfo");
  parse_rule_env(getenv("TRACECAT__MCP_SANDBOX_EGRESS_ALLOWLIST"), &allow_rules);
  parse_rule_env(getenv("TRACECAT__MCP_SANDBOX_EGRESS_DENYLIST"), &deny_rules);
  has_allow_rules = allow_rules.count > 0;
}

static bool host_matches(const char *rule_host, const char *host) {
  if (rule_host[0] == '\0' || host == NULL || host[0] == '\0') {
    return false;
  }
  if (strcmp(rule_host, "*") == 0) {
    return true;
  }
  return strcasecmp(rule_host, host) == 0;
}

static bool port_matches(const egress_rule_t *rule, const char *port) {
  if (!rule->has_port) {
    return true;
  }
  return port != NULL && strcmp(rule->port, port) == 0;
}

static bool list_matches(
    const egress_rule_list_t *rules, const char *host, const char *port) {
  for (size_t i = 0; i < rules->count; ++i) {
    if (host_matches(rules->rules[i].host, host) && port_matches(&rules->rules[i], port)) {
      return true;
    }
  }
  return false;
}

static bool is_blocked_request(const char *host, const char *port) {
  if (list_matches(&deny_rules, host, port)) {
    return true;
  }
  if (has_allow_rules && !list_matches(&allow_rules, host, port)) {
    return true;
  }
  return false;
}

static void sockaddr_to_host_port(
    const struct sockaddr *addr, char *host, size_t host_len, char *port, size_t port_len) {
  host[0] = '\0';
  port[0] = '\0';
  if (addr == NULL) {
    return;
  }
  switch (addr->sa_family) {
    case AF_INET: {
      const struct sockaddr_in *addr4 = (const struct sockaddr_in *)addr;
      inet_ntop(AF_INET, &addr4->sin_addr, host, (socklen_t)host_len);
      snprintf(port, port_len, "%u", ntohs(addr4->sin_port));
      return;
    }
    case AF_INET6: {
      const struct sockaddr_in6 *addr6 = (const struct sockaddr_in6 *)addr;
      inet_ntop(AF_INET6, &addr6->sin6_addr, host, (socklen_t)host_len);
      snprintf(port, port_len, "%u", ntohs(addr6->sin6_port));
      return;
    }
    default:
      return;
  }
}

int getaddrinfo(const char *node, const char *service, const struct addrinfo *hints,
                struct addrinfo **res) {
  pthread_once(&guard_init_once, init_guard);
  if (is_blocked_request(node, service)) {
    return EAI_FAIL;
  }
  return real_getaddrinfo(node, service, hints, res);
}

int connect(int sockfd, const struct sockaddr *addr, socklen_t addrlen) {
  (void)addrlen;
  pthread_once(&guard_init_once, init_guard);
  char host[INET6_ADDRSTRLEN] = {0};
  char port[16] = {0};
  sockaddr_to_host_port(addr, host, sizeof(host), port, sizeof(port));
  if (is_blocked_request(host, port)) {
    errno = EACCES;
    return -1;
  }
  return real_connect(sockfd, addr, addrlen);
}
