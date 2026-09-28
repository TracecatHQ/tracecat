#include <charconv>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>

#include <sys/fsuid.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/types.h>

// Synchronize loop block-device nodes visible to a running executor container.
//
// A container's /dev is normally a private tmpfs populated when the container
// starts. The host kernel can create additional loop devices later, making
// /sys/block/loopN visible in the container without adding the corresponding
// /dev/loopN node. util-linux mount(8) may then select that free loop device but
// fail to open it with ENOENT.
//
// This helper repairs only that visibility mismatch. It mirrors loop devices
// already reported by the kernel into /dev; it does not allocate, configure,
// attach, mount, unmount, or remove loop devices. It is installed with the
// narrow file capabilities because the main Python executor runs as an
// unprivileged user. CAP_SETUID is used only to set the filesystem UID to root
// before publishing nodes, so ordinary UID-1001 action subprocesses cannot open
// node-global loop devices through owner permissions.
//
// Security contract:
//   * accept no caller-controlled paths or device numbers;
//   * trust only kernel sysfs entries named loopN with Linux loop major 7;
//   * reject any existing /dev/loopN path that is not the exact block device;
//   * create root-owned nodes mode 0600, leaving access only to the
//     file-capability mount tool;
//   * fail the entire synchronization on malformed or inconsistent state.
//
// stdout is a small machine-readable ABI consumed by registry_artifacts.py:
//
//     <kernel devices> <created nodes> <existing nodes>\n
//
// Diagnostics go to stderr and a nonzero exit status. Concurrent helper
// processes are safe: only one mknod wins and the EEXIST loser revalidates the
// winning node before accepting it.

namespace {

namespace fs = std::filesystem;

constexpr unsigned int kLoopBlockMajor = 7;
const fs::path kSysBlockPath{"/sys/block"};

struct DeviceNumbers {
    unsigned int major;
    unsigned int minor;
};

struct SyncCounts {
    std::uint64_t kernel_devices = 0;
    std::uint64_t created_nodes = 0;
    std::uint64_t existing_nodes = 0;
};

enum class NodeState { kMissing, kExisting };

class ScopedRootFsuid {
  public:
    ScopedRootFsuid() : original_fsuid_(::setfsuid(0)) {
        // setfsuid(2) reports the previous value even when it refuses a change.
        // Setting the requested value a second time therefore verifies that the
        // first call actually installed root as the filesystem UID.
        if (::setfsuid(0) != 0) {
            throw std::runtime_error("cannot set root filesystem uid");
        }
    }

    ScopedRootFsuid(const ScopedRootFsuid&) = delete;
    ScopedRootFsuid& operator=(const ScopedRootFsuid&) = delete;

    ~ScopedRootFsuid() { static_cast<void>(::setfsuid(original_fsuid_)); }

  private:
    uid_t original_fsuid_;
};

std::optional<unsigned int> parse_loop_minor(const fs::path& filename) {
    const std::string name = filename.native();
    constexpr std::string_view prefix{"loop"};
    if (!name.starts_with(prefix) || name.size() == prefix.size()) {
        return std::nullopt;
    }

    const std::string_view digits{name.data() + prefix.size(),
                                  name.size() - prefix.size()};
    unsigned int minor = 0;
    const auto [end, error] =
        std::from_chars(digits.data(), digits.data() + digits.size(), minor);
    if (error != std::errc{} || end != digits.data() + digits.size()) {
        return std::nullopt;
    }
    return minor;
}

unsigned int parse_device_number(std::string_view value,
                                 const fs::path& source) {
    unsigned int number = 0;
    const auto [end, error] =
        std::from_chars(value.data(), value.data() + value.size(), number);
    if (error != std::errc{} || end != value.data() + value.size()) {
        throw std::runtime_error("invalid device numbers in " + source.string());
    }
    return number;
}

DeviceNumbers read_device_numbers(const fs::path& sysfs_device,
                                  unsigned int expected_minor) {
    const fs::path source = sysfs_device / "dev";
    std::ifstream input{source};
    std::string value;
    if (!input || !std::getline(input, value)) {
        throw std::runtime_error("cannot read " + source.string());
    }

    const std::size_t separator = value.find(':');
    if (separator == std::string::npos ||
        value.find(':', separator + 1) != std::string::npos) {
        throw std::runtime_error("invalid device numbers in " + source.string());
    }

    // Validate both halves of the mapping. The directory name supplies the
    // destination /dev suffix, while the sysfs dev file supplies the block
    // device identity used by mknod(2); they must describe the same loop node.
    const DeviceNumbers numbers{
        .major = parse_device_number(
            std::string_view{value}.substr(0, separator), source),
        .minor = parse_device_number(
            std::string_view{value}.substr(separator + 1), source),
    };
    if (numbers.major != kLoopBlockMajor ||
        numbers.minor != expected_minor) {
        throw std::runtime_error("unexpected loop device numbers in " +
                                 source.string());
    }
    return numbers;
}

NodeState validate_existing_node(const fs::path& device,
                                 const DeviceNumbers& numbers) {
    struct stat node_stat {};
    // lstat(2), rather than stat(2), deliberately rejects a symlink occupying
    // /dev/loopN instead of following it to an attacker-selected target.
    if (::lstat(device.c_str(), &node_stat) != 0) {
        if (errno == ENOENT) {
            return NodeState::kMissing;
        }
        throw std::system_error(errno, std::generic_category(),
                                "cannot inspect " + device.string());
    }

    if (!S_ISBLK(node_stat.st_mode) ||
        ::major(node_stat.st_rdev) != numbers.major ||
        ::minor(node_stat.st_rdev) != numbers.minor) {
        throw std::runtime_error("refusing unexpected existing path " +
                                 device.string());
    }
    return NodeState::kExisting;
}

bool create_device_node(const fs::path& device,
                        const DeviceNumbers& numbers) {
    if (::mknod(device.c_str(), S_IFBLK | 0600,
                ::makedev(numbers.major, numbers.minor)) == 0) {
        return true;
    }

    const int error = errno;
    // Another executor thread or process may create this container-local node
    // after our lstat and before mknod. Treat that race as success only after
    // applying the same strict block-device validation as the fast path.
    if (error == EEXIST &&
        validate_existing_node(device, numbers) == NodeState::kExisting) {
        return false;
    }
    throw std::system_error(error, std::generic_category(),
                            "cannot create " + device.string());
}

SyncCounts synchronize_loop_devices() {
    SyncCounts counts;
    // /sys/block is the authoritative view of loop objects currently known to
    // the shared kernel. Non-loop block devices are ignored by name.
    for (const fs::directory_entry& entry :
         fs::directory_iterator{kSysBlockPath}) {
        const std::optional<unsigned int> minor =
            parse_loop_minor(entry.path().filename());
        if (!minor.has_value()) {
            continue;
        }
        ++counts.kernel_devices;

        const DeviceNumbers numbers =
            read_device_numbers(entry.path(), minor.value());
        const fs::path device =
            fs::path{"/dev"} / ("loop" + std::to_string(numbers.minor));
        if (validate_existing_node(device, numbers) == NodeState::kExisting) {
            ++counts.existing_nodes;
        } else if (create_device_node(device, numbers)) {
            ++counts.created_nodes;
        } else {
            ++counts.existing_nodes;
        }
    }
    return counts;
}

}  // namespace

int main(int argc, char*[]) {
    // A zero-argument interface keeps the capability-bearing helper
    // fixed-purpose: callers cannot ask it to create an arbitrary device node.
    if (argc != 1) {
        std::cerr << "tracecat-loop-device-sync does not accept arguments\n";
        return EXIT_FAILURE;
    }

    try {
        // mknod(2) assigns ownership from the caller's filesystem UID. Publish
        // nodes as root from birth so there is no UID-1001 ownership window for
        // a concurrent untrusted action to exploit.
        const ScopedRootFsuid root_fsuid;
        const SyncCounts counts = synchronize_loop_devices();
        std::cout << counts.kernel_devices << ' ' << counts.created_nodes << ' '
                  << counts.existing_nodes << '\n';
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
