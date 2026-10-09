import { syncSettingsSchema } from "@/components/workspace-sync/connection-form"
import { getConfiguredGitProviders } from "@/hooks/use-git-sync-providers"
import {
  type GitRemoteTarget,
  getAppRepositoryGitUrl,
  getBaseUrlHost,
  getBaseUrlPath,
  parseGitRemote,
} from "@/lib/git-remote"

const github: GitRemoteTarget = {
  provider: "github",
  host: "github.com",
  hostIsKnown: true,
  orgConfigured: true,
}
const gitlab: GitRemoteTarget = {
  provider: "gitlab",
  host: "gitlab.example.com",
  hostIsKnown: true,
  orgConfigured: true,
}
const bitbucket: GitRemoteTarget = {
  provider: "bitbucket",
  host: "bitbucket.org",
  hostIsKnown: true,
  orgConfigured: false,
}
const dataCenter: GitRemoteTarget = {
  provider: "bitbucket_data_center",
  host: "bitbucket.example.com",
  hostIsKnown: true,
  orgConfigured: true,
}

function expectGitUrl(input: string, target: GitRemoteTarget, url: string) {
  const result = parseGitRemote(input, target)
  expect(result).toMatchObject({ kind: "ok", remote: { gitUrl: url } })
  expect(
    syncSettingsSchema.safeParse({
      git_provider: target.provider,
      git_repo_url: url,
    }).success
  ).toBe(true)
}

function expectError(input: string, target: GitRemoteTarget, message: string) {
  expect(parseGitRemote(input, target)).toEqual({ kind: "invalid", message })
}

describe("parseGitRemote", () => {
  it("treats blank input as empty", () => {
    expect(parseGitRemote("   ", github)).toEqual({ kind: "empty" })
  })

  it.each([
    "TracecatHQ/detections",
    "https://github.com/TracecatHQ/detections",
    "https://github.com/TracecatHQ/detections/tree/main/workflows",
    "https://www.github.com/TracecatHQ/detections.git/",
    "github.com/TracecatHQ/detections/blob/main/README.md",
    "git@github.com:TracecatHQ/detections.git",
    "git+ssh://git@github.com/TracecatHQ/detections.git",
    "ssh://git@github.com/TracecatHQ/detections",
  ])("resolves GitHub input %s", (input) => {
    expectGitUrl(
      input,
      github,
      "git+ssh://git@github.com/TracecatHQ/detections.git"
    )
  })

  it("keeps an explicit ref from a git+ssh URL", () => {
    const result = parseGitRemote(
      "git+ssh://git@github.com/TracecatHQ/detections.git@release/v2",
      github
    )
    expect(result).toEqual({
      kind: "ok",
      remote: {
        gitUrl: "git+ssh://git@github.com/TracecatHQ/detections.git@release/v2",
        host: "github.com",
        path: "TracecatHQ/detections",
        ref: "release/v2",
      },
    })
  })

  it("rejects a host the organization has not set up", () => {
    expectError(
      "https://gitlab.com/TracecatHQ/detections",
      github,
      "gitlab.com isn't set up for this organization. GitHub here is github.com."
    )
  })

  it("accepts another GitHub host when the host is not known", () => {
    expectGitUrl(
      "https://github.example.com/TracecatHQ/detections",
      { ...github, hostIsKnown: false },
      "git+ssh://git@github.example.com/TracecatHQ/detections.git"
    )
  })

  it("rejects a GitHub path without a repository", () => {
    expectError(
      "TracecatHQ",
      github,
      "Enter owner/repository or a GitHub repository URL."
    )
  })

  it.each([
    "group/sub/project",
    "https://gitlab.example.com/group/sub/project",
    "https://gitlab.example.com/group/sub/project/-/tree/main",
    "https://gitlab.example.com/group/sub/project.git",
    "gitlab.example.com/group/sub/project",
    "git@gitlab.example.com:group/sub/project.git",
  ])("keeps GitLab nested groups for %s", (input) => {
    expectGitUrl(
      input,
      gitlab,
      "git+ssh://git@gitlab.example.com/group/sub/project.git"
    )
  })

  it("reads a dotted first segment as a GitLab group on a known host", () => {
    expectGitUrl(
      "security.tools/platform/project",
      gitlab,
      "git+ssh://git@gitlab.example.com/security.tools/platform/project.git"
    )
  })

  it("keeps www. on a self-hosted host", () => {
    expectGitUrl(
      "https://www.gitlab.example.com/group/project",
      { ...gitlab, host: "www.gitlab.example.com" },
      "git+ssh://git@www.gitlab.example.com/group/project.git"
    )
  })

  it("reads www. before the GitLab host as that host", () => {
    for (const input of [
      "www.gitlab.example.com/group/project",
      "https://www.gitlab.example.com/group/project",
    ]) {
      expectGitUrl(
        input,
        gitlab,
        "git+ssh://git@gitlab.example.com/group/project.git"
      )
    }
  })

  it("drops a GitLab base path from browser URLs only", () => {
    const target = { ...gitlab, basePath: "/gitlab" }
    expectGitUrl(
      "https://gitlab.example.com/gitlab/group/project/-/tree/main",
      target,
      "git+ssh://git@gitlab.example.com/group/project.git"
    )
    expectGitUrl(
      "git@gitlab.example.com:gitlab/project.git",
      target,
      "git+ssh://git@gitlab.example.com/gitlab/project.git"
    )
  })

  it("keeps a GitLab SSH port", () => {
    expectGitUrl(
      "ssh://git@gitlab.example.com:2222/group/project.git",
      gitlab,
      "git+ssh://git@gitlab.example.com:2222/group/project.git"
    )
  })

  it("rejects a GitLab host that is not the organization's", () => {
    expectError(
      "https://gitlab.com/group/project",
      gitlab,
      "gitlab.com isn't set up for this organization. GitLab here is gitlab.example.com."
    )
  })

  it("asks for a full URL when the GitLab host is unknown", () => {
    const target = {
      ...gitlab,
      host: undefined,
      hostIsKnown: false,
      orgConfigured: false,
    }
    expectError(
      "group/project",
      target,
      "Paste the full repository URL from GitLab."
    )
    expectGitUrl(
      "https://gitlab.example.com/group/project",
      target,
      "git+ssh://git@gitlab.example.com/group/project.git"
    )
  })

  it.each([
    "example-workspace/example-repo",
    "https://bitbucket.org/example-workspace/example-repo/src/main/",
    "https://user@bitbucket.org/example-workspace/example-repo.git",
    "git@bitbucket.org:example-workspace/example-repo.git",
  ])("resolves Bitbucket Cloud input %s", (input) => {
    expectGitUrl(
      input,
      bitbucket,
      "git+ssh://git@bitbucket.org/example-workspace/example-repo.git"
    )
  })

  it("rejects non-bitbucket.org hosts for Bitbucket Cloud", () => {
    expectError(
      "https://bitbucket.example.com/example-workspace/example-repo",
      bitbucket,
      "Bitbucket Cloud repositories here are on bitbucket.org, not bitbucket.example.com."
    )
  })

  it.each([
    "PROJ/detections",
    "https://bitbucket.example.com/projects/PROJ/repos/detections/browse",
    "https://bitbucket.example.com/context/projects/PROJ/repos/detections",
    "https://bitbucket.example.com/scm/PROJ/detections.git",
  ])("resolves Bitbucket Data Center input %s", (input) => {
    expectGitUrl(
      input,
      dataCenter,
      "git+ssh://git@bitbucket.example.com/PROJ/detections.git"
    )
  })

  it("keeps a pasted Bitbucket Data Center git+ssh URL on the instance host", () => {
    expectGitUrl(
      "git+ssh://git@bitbucket.example.com/PROJ/detections.git",
      dataCenter,
      "git+ssh://git@bitbucket.example.com/PROJ/detections.git"
    )
  })

  it("rejects a Bitbucket Data Center SSH URL on another host", () => {
    expect(
      parseGitRemote(
        "git+ssh://git@ssh.bitbucket.example.com/PROJ/detections.git",
        dataCenter
      )
    ).toEqual({
      kind: "invalid",
      message:
        "ssh.bitbucket.example.com isn't set up for this organization. Bitbucket Data Center here is bitbucket.example.com.",
    })
  })

  it("rejects a Bitbucket Data Center SSH port the backend refuses", () => {
    expectError(
      "git+ssh://git@bitbucket.example.com:7999/PROJ/detections.git",
      dataCenter,
      "Remove the port. Bitbucket takes it from the organization's instance URL."
    )
  })

  it("rejects nested Bitbucket Data Center paths", () => {
    expectError(
      "git+ssh://git@bitbucket.example.com/PROJ/sub/detections.git",
      dataCenter,
      "Enter PROJECT/repository or a Bitbucket Data Center repository URL."
    )
  })
})

describe("git remote helpers", () => {
  it("reads hosts from provider base URLs", () => {
    expect(getBaseUrlHost("https://gitlab.example.com:8443/gitlab")).toBe(
      "gitlab.example.com"
    )
    expect(getBaseUrlHost("not a url")).toBeUndefined()
  })

  it("reads base paths from provider base URLs", () => {
    expect(getBaseUrlPath("https://gitlab.example.com/gitlab/")).toBe("/gitlab")
    expect(getBaseUrlPath("https://gitlab.example.com")).toBeUndefined()
  })

  it("pins a non-main default branch for app repositories", () => {
    const repository = {
      id: 1,
      name: "playbooks",
      full_name: "TracecatHQ/playbooks",
      private: false,
      default_branch: "trunk",
      git_url: "git+ssh://git@github.com/TracecatHQ/playbooks.git",
      html_url: "https://github.com/TracecatHQ/playbooks",
      installation_id: 1,
      installation_account: "TracecatHQ",
    }
    expect(getAppRepositoryGitUrl(repository)).toBe(
      "git+ssh://git@github.com/TracecatHQ/playbooks.git@trunk"
    )
    expect(
      getAppRepositoryGitUrl({ ...repository, default_branch: "main" })
    ).toBe("git+ssh://git@github.com/TracecatHQ/playbooks.git")
  })
})

describe("getConfiguredGitProviders", () => {
  it("lists only usable providers with their hosts", () => {
    expect(
      getConfiguredGitProviders({
        github: { exists: true },
        gitlab: { exists: true, base_url: "https://gitlab.example.com" },
        bitbucket: { exists: true, is_corrupted: true },
        bitbucketDataCenter: { exists: false },
      })
    ).toEqual([{ id: "github" }, { id: "gitlab", host: "gitlab.example.com" }])
  })
})
