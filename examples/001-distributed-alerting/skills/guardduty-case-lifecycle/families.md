# Finding families

Test the finding `type` against these rules in order. The first rule that matches decides the family. Each pattern is a full-string regular expression. The family is used for the `family-<family>` and `group-<family>-<account>` tags, and to choose the family in hypothesis-library.md.

| Order | Family | Full-string pattern | Examples |
|---|---|---|---|
| 1 | `k8s-operator` | `^[^:]+:Kubernetes/.*$` | `Execution:Kubernetes/ExecInKubeSystemPod` |
| 2 | `compute-dns` | `^(.*!DNS\|Trojan:EC2/DGADomainRequest[.].*\|Trojan:EC2/DNSDataExfiltration\|UnauthorizedAccess:EC2/MetadataDNSRebind)$` | `Trojan:EC2/BlackholeTraffic!DNS`, `Trojan:EC2/DGADomainRequest.B` |
| 3 | `compute-network` | `^((Trojan\|Backdoor\|CryptoCurrency\|DefenseEvasion\|Impact\|Behavior):EC2/.*\|UnauthorizedAccess:EC2/(?!.*BruteForce).*)$` | `Backdoor:EC2/C&CActivity.B`, `DefenseEvasion:EC2/UnusualDoHActivity` |
| 4 | `credential-use` | `^((?!Policy:\|Stealth:\|DefenseEvasion:)[^:]+:IAMUser/.*\|.*InstanceCredentialExfiltration.*\|CredentialAccess:.*\|PenTest:.*)$` | `Discovery:IAMUser/AnomalousBehavior`, `UnauthorizedAccess:IAMUser/InstanceCredentialExfiltration.OutsideAWS` |
| 5 | `config-change` | `^(Policy:.*\|Stealth:.*\|Impact:S3/.*\|DefenseEvasion:IAMUser/.*)$` | `Policy:S3/BucketBlockPublicAccessDisabled`, `Stealth:IAMUser/CloudTrailLoggingDisabled` |
| 6 | `inbound-probe` | `^(Recon:EC2/.*\|UnauthorizedAccess:EC2/.*BruteForce.*\|.*PortProbe.*)$` | `Recon:EC2/PortProbeUnprotectedPort`, `UnauthorizedAccess:EC2/SSHBruteForce` |
| 7 | `generic` | anything else | |

Within a table cell, `\|` is a literal alternation bar `|`. The order matters:

- `UnauthorizedAccess:EC2/SSHBruteForce` is `inbound-probe`, because rule 3 excludes `BruteForce`.
- `DefenseEvasion:IAMUser/...` is `config-change`, because rule 4 excludes `DefenseEvasion:`.
- A `Kubernetes/` type is always `k8s-operator`, whatever its prefix.
