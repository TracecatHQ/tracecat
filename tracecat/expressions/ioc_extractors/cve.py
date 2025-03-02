import re

# CVE (Common Vulnerabilities and Exposures)
CVE_REGEX = r"CVE-\d{4}-\d{4,7}"


def extract_cves(text: str) -> list[str]:
    """Extract CVE IDs, such as CVE-2021-34527, from a string."""
    return list(set(re.findall(CVE_REGEX, text)))
