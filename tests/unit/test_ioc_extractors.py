import pytest

from tracecat.expressions.ioc_extractors import (
    extract_asns,
    extract_cves,
    extract_domains,
    extract_emails,
    extract_ipv4_addresses,
    extract_ipv6_addresses,
    extract_mac_addresses,
    extract_md5_hashes,
    extract_sha1_hashes,
    extract_sha256_hashes,
    extract_sha512_hashes,
    extract_urls,
    normalize_email,
)

### IOC EXTRACTORS


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "Detected suspicious traffic from AS12345 to our network.",
            ["AS12345"],
        ),
        (
            "Multiple ASNs detected: AS1234, AS4321, and AS56789.",
            ["AS1234", "AS4321", "AS56789"],
        ),
        (
            "No ASNs in this text.",
            [],
        ),
        (
            "Some invalid formats: 12345AS, ASabcde",
            [],
        ),
        (
            "ASN in JSON data: {'asn': 'AS15169', 'org': 'Google LLC'}",
            ["AS15169"],
        ),
        (
            "Threat report: Malicious traffic from AS4134 (China Telecom) detected targeting port 445",
            ["AS4134"],
        ),
        (
            "Network analysis showed connections to AS16509 (Amazon), AS8075 (Microsoft), and AS15169 (Google)",
            ["AS16509", "AS8075", "AS15169"],
        ),
        (
            "BGP routing anomaly detected when AS13335 (Cloudflare) announced prefixes belonging to AS7018 (AT&T)",
            ["AS13335", "AS7018"],
        ),
        (
            "Blocklist update: Added AS199524 and AS14618 due to repeated abuse",
            ["AS199524", "AS14618"],
        ),
        (
            "Large-scale scanning from the following sources: ASN 45090, AS4837, AS17621",
            ["AS4837", "AS17621"],  # ASN 45090 without the "AS" prefix shouldn't match
        ),
        (
            "Security advisory: {'affected_asns': ['AS3356', 'AS6939', 'AS174'], 'vulnerability': 'BGP hijacking'}",
            ["AS3356", "AS6939", "AS174"],
        ),
        (
            "Historical scanning report: Host 192.168.1.1 (AS9121) scanned 1433/TCP across multiple targets",
            ["AS9121"],
        ),
        (
            "IP: 104.18.11.208, ASN: AS13335, Organization: Cloudflare, Inc.",
            ["AS13335"],
        ),
        (
            "Deep packet inspection found data exfiltration to command servers in AS4812 (China Telecom) and AS4134",
            ["AS4812", "AS4134"],
        ),
    ],
    ids=[
        "single_asn",
        "multiple_asns",
        "no_asns",
        "invalid_formats",
        "asn_in_json",
        "asn_with_organization",
        "multiple_asns_with_orgs",
        "bgp_routing_anomaly",
        "blocklist_entry",
        "asn_without_prefix",
        "asns_in_structured_security_advisory",
        "asn_in_scanning_report",
        "asn_in_ip_enrichment",
        "data_exfiltration_to_asns",
    ],
)
def test_extract_asns(text: str, expected: list[str]) -> None:
    extracted = extract_asns(text)
    assert sorted(extracted) == sorted(expected)


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "Critical vulnerability CVE-2021-34527 needs immediate patching.",
            ["CVE-2021-34527"],
        ),
        (
            "Multiple CVEs detected: CVE-2021-44228, CVE-2022-22965, and CVE-2023-12345.",
            ["CVE-2021-44228", "CVE-2022-22965", "CVE-2023-12345"],
        ),
        (
            "No CVEs in this text.",
            [],
        ),
        (
            "Some invalid formats: CVE-abcd-1234, CVE-2021, CVE-2021-ABCD.",
            [],
        ),
        (
            "CVE in JSON data: {'cve_id': 'CVE-2021-44228', 'severity': 'critical'}",
            ["CVE-2021-44228"],
        ),
        (
            "CVE with many digits: CVE-2021-1234567",
            ["CVE-2021-1234567"],
        ),
    ],
    ids=[
        "single_cve",
        "multiple_cves",
        "no_cves",
        "invalid_formats",
        "cve_in_json",
        "long_cve_id",
    ],
)
def test_extract_cves(text: str, expected: list[str]) -> None:
    extracted = extract_cves(text)
    assert sorted(extracted) == sorted(expected)


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "Malicious domain detected: evil-domain.com in traffic logs.",
            ["evil-domain.com"],
        ),
        (
            "Multiple domains: example.com, sub.example.org, and test-site.co.uk.",
            ["example.com", "sub.example.org", "test-site.co.uk"],
        ),
        (
            "No domains in this text, only an IP 192.168.1.1.",
            [],
        ),
        (
            "Some domain in URL: https://example.com/path and domain with text example.org.",
            ["example.org"],
        ),
        (
            "Domain in JSON: {'domain': 'malware.net', 'first_seen': '2023-01-01'}",
            ["malware.net"],
        ),
        (
            "Subdomain with many levels: sub.sub2.example.com",
            ["sub.sub2.example.com"],
        ),
        (
            # This should not match as valid domain
            "Domain with numeric TLD: example.123",
            [],
        ),
        (
            "Domain with hyphens: this-is-a-valid-domain-name.com",
            ["this-is-a-valid-domain-name.com"],
        ),
        (
            "Domain with very long subdomain: thisisaveryveryveryveryveryveryveryverylongsubdomain.example.com",
            ["thisisaveryveryveryveryveryveryveryverylongsubdomain.example.com"],
        ),
        (
            "Multiple subdomains: a.b.c.d.e.f.g.example.com",
            ["a.b.c.d.e.f.g.example.com"],
        ),
        (
            "TLDs: example.app, malicious.xyz, test.tech",
            ["example.app", "malicious.xyz", "test.tech"],
        ),
        (
            "Domain with trailing dot (DNS root): example.com.",
            ["example.com"],
        ),
        (
            "Typosquatting domain: g00gle.com",
            ["g00gle.com"],
        ),
        (
            "Domain in security alert JSON: {'alert_type': 'dns_request', 'details': {'requested_domain': 'malware-delivery.net'}}",
            ["malware-delivery.net"],
        ),
        (
            "Domains in multiple JSON levels: {'data': {'threats': [{'domain': 'bad.com'}, {'domain': 'worse.net'}]}}",
            ["bad.com", "worse.net"],
        ),
        (
            "Domain extraction from email address: user@malicious-domain.com",
            ["malicious-domain.com"],
        ),
        (
            "Punycode domain: xn--80acd1bdbrs.xn--p1ai",
            ["xn--80acd1bdbrs.xn--p1ai"],
        ),
        (
            "Country-code TLD: suspicious.cn, malware.ru, exploit.kr",
            ["suspicious.cn", "malware.ru", "exploit.kr"],
        ),
    ],
    ids=[
        "single_domain",
        "multiple_domains",
        "no_domains",
        "domain_in_url",
        "domain_in_json",
        "multi_level_subdomain",
        "domain_with_numeric_tld",
        "domain_with_hyphens",
        "very_long_subdomain",
        "multiple_subdomains",
        "tlds",
        "domain_with_trailing_dot",
        "typosquatting_domain",
        "domain_in_security_alert",
        "domains_in_multiple_json_levels",
        "domain_from_email",
        "punycode_domain",
        "country_code_tlds",
    ],
)
def test_extract_domains(text: str, expected: list[str]) -> None:
    extracted = extract_domains(text)
    assert sorted(extracted) == sorted(expected)


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "MD5 hash: d41d8cd98f00b204e9800998ecf8427e found in sample.",
            ["d41d8cd98f00b204e9800998ecf8427e"],
        ),
        (
            "Multiple MD5s: e10adc3949ba59abbe56e057f20f883e, 5f4dcc3b5aa765d61d8327deb882cf99.",
            ["e10adc3949ba59abbe56e057f20f883e", "5f4dcc3b5aa765d61d8327deb882cf99"],
        ),
        (
            "No MD5 hashes here.",
            [],
        ),
        (
            "Invalid hash: d41d8cd98f00b204e9800998ecf842 (too short)",
            [],
        ),
        (
            "MD5 in JSON: {'md5': 'c4ca4238a0b923820dcc509a6f75849b', 'detected': true}",
            ["c4ca4238a0b923820dcc509a6f75849b"],
        ),
        (
            "Alert from AV: Malware detected! MD5: 1a79a4d60de6718e8e5b326e338ae533 - Trojan.Generic",
            ["1a79a4d60de6718e8e5b326e338ae533"],
        ),
        (
            "VirusTotal report: {'scans': {'file_hash': '81dc9bdb52d04dc20036dbd8313ed055', 'score': '32/70'}}",
            ["81dc9bdb52d04dc20036dbd8313ed055"],
        ),
        (
            "Multiple hash formats: MD5(sample.exe) = d8cd98f00b204e9800998ecf8427e, SHA1 = 2fd4e1c67a2d28fced849ee1bb76e7391b93eb12",
            [],  # No valid MD5 hash here (first is incomplete)
        ),
        (
            "MD5 in file scan: sample01.exe 44d88612fea8a8f36de82e1278abb02f SUSPICIOUS",
            ["44d88612fea8a8f36de82e1278abb02f"],
        ),
        (
            "Mixed case MD5: fF9cD4H9jC9hF9tD4cC9eE9vR9gT8jU7 and valid ac3478d69a3c81fa62e60f5c3696165a",
            [
                "ac3478d69a3c81fa62e60f5c3696165a"
            ],  # First one isn't a valid MD5 (contains non-hex chars)
        ),
        (
            "MD5 in nested alert JSON: {'results': {'files': [{'name': 'malware.exe', 'analysis': {'hash': {'md5': 'c9f0f895fb98ab9159f51fd0297e236d'}}}]}}",
            ["c9f0f895fb98ab9159f51fd0297e236d"],
        ),
        (
            "Sandbox report: extracted payload 8f10688DD41A3BB9E714FF5C718D3D6A, contacted C2 at 192.168.1.100",
            ["8f10688dd41a3bb9e714ff5c718d3d6a"],  # MD5s are case-insensitive
        ),
    ],
    ids=[
        "single_md5",
        "multiple_md5s",
        "no_md5s",
        "invalid_md5_length",
        "md5_in_json",
        "av_alert_md5",
        "virustotal_report_md5",
        "invalid_md5_with_identifier",
        "md5_in_file_scan",
        "mixed_case_and_invalid_md5",
        "md5_in_nested_json",
        "md5_uppercase_in_report",
    ],
)
def test_extract_md5_hashes(text: str, expected: list[str]) -> None:
    extracted = extract_md5_hashes(text)
    assert sorted([h.lower() for h in extracted]) == sorted(
        [h.lower() for h in expected]
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "SHA1 hash: da39a3ee5e6b4b0d3255bfef95601890afd80709 in file.",
            ["da39a3ee5e6b4b0d3255bfef95601890afd80709"],
        ),
        (
            "Multiple SHA1s: 40bd001563085fc35165329ea1ff5c5ecbdbbeef, 5baa61e4c9b93f3f0682250b6cf8331b7ee68fd8.",
            [
                "40bd001563085fc35165329ea1ff5c5ecbdbbeef",
                "5baa61e4c9b93f3f0682250b6cf8331b7ee68fd8",
            ],
        ),
        (
            "No SHA1 hashes here.",
            [],
        ),
        (
            "Invalid hash: 40bd001563085fc35165329ea1ff5c5ecbdbbe (too short)",
            [],
        ),
        (
            "SHA1 in JSON: {'sha1': 'aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d', 'detected': true}",
            ["aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d"],
        ),
        (
            "IOC Report: SHA1 hash 6367c48dd193d56ea7b0baad25b19455e529f5ee associated with Ransomware",
            ["6367c48dd193d56ea7b0baad25b19455e529f5ee"],
        ),
        (
            "Certificate fingerprint: SHA1: D6EA85D98E91C6F4C748CD977F134B3A3C0F0B14",
            ["D6EA85D98E91C6F4C748CD977F134B3A3C0F0B14"],
        ),
        (
            "Multiple algorithms: MD5=827ccb0eea8a706c4c34a16891f84e7b SHA1=7c4a8d09ca3762af61e59520943dc26494f8941b",
            ["7c4a8d09ca3762af61e59520943dc26494f8941b"],
        ),
        (
            "SHA1 in threat intelligence feed: {'indicator': {'type': 'file', 'sha1': 'f1d2d2f924e986ac86fdf7b36c94bcdf32beec15'}}",
            ["f1d2d2f924e986ac86fdf7b36c94bcdf32beec15"],
        ),
        (
            "SHA1 with prefix notation: SHA1=2fd4e1c67a2d28fced849ee1bb76e7391b93eb12, Malicious",
            ["2fd4e1c67a2d28fced849ee1bb76e7391b93eb12"],
        ),
        (
            "Command output: $ sha1sum malware.bin\n356a192b7913b04c54574d18c28d46e6395428ab  malware.bin",
            ["356a192b7913b04c54574d18c28d46e6395428ab"],
        ),
        (
            "DFIR report: {'evidence': {'executables': [{'path': '/tmp/backdoor', 'sha1': 'da39a3ee5e6b4b0d3255bfef95601890afd80709', 'compiled': '2023-01-15'}]}}",
            ["da39a3ee5e6b4b0d3255bfef95601890afd80709"],
        ),
    ],
    ids=[
        "single_sha1",
        "multiple_sha1s",
        "no_sha1s",
        "invalid_sha1_length",
        "sha1_in_json",
        "ioc_report_sha1",
        "certificate_fingerprint",
        "multiple_algorithm_hashes",
        "sha1_in_threat_intel",
        "sha1_with_prefix",
        "sha1_command_output",
        "sha1_in_dfir_report",
    ],
)
def test_extract_sha1_hashes(text: str, expected: list[str]) -> None:
    extracted = extract_sha1_hashes(text)
    assert sorted([h.lower() for h in extracted]) == sorted(
        [h.lower() for h in expected]
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "SHA256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 in malware.",
            ["e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
        ),
        (
            "Multiple: 8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92, 5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8.",
            [
                "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92",
                "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
            ],
        ),
        (
            "No SHA256 hashes here.",
            [],
        ),
        (
            "Invalid: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b8 (too short)",
            [],
        ),
        (
            "SHA256: {'hash': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'}",
            ["e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
        ),
        (
            "Threat hunting results: SHA256 hash cf80cd8aed482d5d1527d7dc72fceff84e6326592848447d2dc0b0e87dfc9a90 flagged malicious",
            ["cf80cd8aed482d5d1527d7dc72fceff84e6326592848447d2dc0b0e87dfc9a90"],
        ),
        (
            "Code signing certificate: SHA256 Fingerprint=86D88A5AE39C89B270DA72C0C955CC49BD46E5CF16B4331CAAFF9E5B4748ECAF",
            ["86D88A5AE39C89B270DA72C0C955CC49BD46E5CF16B4331CAAFF9E5B4748ECAF"],
        ),
        (
            "Windows Defender alert: SHA256: C6FB8EE5EB18C7F0AD3E8F394C8A32BFF06D9013FEBE41D0E8BDB5E259BF5B4E",
            ["C6FB8EE5EB18C7F0AD3E8F394C8A32BFF06D9013FEBE41D0E8BDB5E259BF5B4E"],
        ),
        (
            "Multiple hashes in sandbox report: {'results': {'sample': {'md5': 'c4ca4238a0b923820dcc509a6f75849b', 'sha1': '356a192b7913b04c54574d18c28d46e6395428ab', 'sha256': 'b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9'}}}",
            ["b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"],
        ),
        (
            "Command output: $ sha256sum implant.exe\ne3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  implant.exe",
            ["e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
        ),
        (
            "Incident response: found malware dropper with SHA256=9F86D081884C7D659A2FEAA0C55AD015A3BF4F1B2B0B822CD15D6C15B0F00A08 on host",
            ["9F86D081884C7D659A2FEAA0C55AD015A3BF4F1B2B0B822CD15D6C15B0F00A08"],
        ),
        (
            "YARA match: rule Ransomware {strings: $a = 'ENCRYPTED' condition: $a} matched file with SHA256 hash 4B227777D4DD1FC61C6F884F48641D02B4D121D3FD328CB08B5531FCACDABF8A",
            ["4B227777D4DD1FC61C6F884F48641D02B4D121D3FD328CB08B5531FCACDABF8A"],
        ),
        (
            "Threat intel report mentions a multi-stage attack using files ab5df625bc76c4157d8f8899e50b6b1f36b9a186d5c9cf8f3ce21162feae5712 and ef2d127de37b942baad06145e54b0c619a1f22327b2ebbcfbec78f5564afe39d as payloads",
            [
                "ab5df625bc76c4157d8f8899e50b6b1f36b9a186d5c9cf8f3ce21162feae5712",
                "ef2d127de37b942baad06145e54b0c619a1f22327b2ebbcfbec78f5564afe39d",
            ],
        ),
    ],
    ids=[
        "single_sha256",
        "multiple_sha256s",
        "no_sha256s",
        "invalid_sha256_length",
        "sha256_in_json",
        "threat_hunting_sha256",
        "certificate_sha256_fingerprint",
        "defender_alert_sha256",
        "multiple_hash_algorithms",
        "sha256_command_output",
        "sha256_with_prefix",
        "sha256_in_yara_match",
        "sha256_in_threat_intel_text",
    ],
)
def test_extract_sha256_hashes(text: str, expected: list[str]) -> None:
    extracted = extract_sha256_hashes(text)
    assert sorted(extracted) == sorted(expected)


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "SHA512: cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e in malware.",
            [
                "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e"
            ],
        ),
        (
            "Multiple: b109f3bbbc244eb82441917ed06d618b9008dd09b3befd1b5e07394c706a8bb980b1d7785e5976ec049b46df5f1326af5a2ea6d103fd07c95385ffab0cacbc86, ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f.",
            [
                "b109f3bbbc244eb82441917ed06d618b9008dd09b3befd1b5e07394c706a8bb980b1d7785e5976ec049b46df5f1326af5a2ea6d103fd07c95385ffab0cacbc86",
                "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
            ],
        ),
        (
            "No SHA512 hashes here.",
            [],
        ),
        (
            "Invalid: cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927d (too short)",
            [],
        ),
        (
            "SHA512: {'hash': 'cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e'}",
            [
                "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e"
            ],
        ),
        (
            "Threat hunting results: SHA512 hash 204a8fc6dda82f0a0ced7eb905fd23c71d38b40262358d8d61f6b7177dbc4217c3dca86f72cfb424ea6c1775ebe4c40fd339265cc284278c08d8c7c29ab9a119 flagged malicious",
            [
                "204a8fc6dda82f0a0ced7eb905fd23c71d38b40262358d8d61f6b7177dbc4217c3dca86f72cfb424ea6c1775ebe4c40fd339265cc284278c08d8c7c29ab9a119"
            ],
        ),
        (
            "Code signing certificate: SHA512 Fingerprint=3C9909AFEC25354D551DAE21590BB26E38D53F2173B8D3DC3EEE4C047E7AB1C1EB8B85103E3BE7BA613B31BB5C9C36214DC9F14A42FD7A2FDB84856BCA5C44C2",
            [
                "3C9909AFEC25354D551DAE21590BB26E38D53F2173B8D3DC3EEE4C047E7AB1C1EB8B85103E3BE7BA613B31BB5C9C36214DC9F14A42FD7A2FDB84856BCA5C44C2"
            ],
        ),
        (
            "Windows Defender alert: SHA512: 87AA7CDEA5EF619D4FF0B4241A1D6CB02379F4E7D07789B442D8D80EF6166F1C2B474C8358CFB120FB03D18C744DCCBF0191B3E6C386D275904721916B6209E0",
            [
                "87AA7CDEA5EF619D4FF0B4241A1D6CB02379F4E7D07789B442D8D80EF6166F1C2B474C8358CFB120FB03D18C744DCCBF0191B3E6C386D275904721916B6209E0"
            ],
        ),
        (
            "Multiple hashes in sandbox report: {'results': {'sample': {'md5': 'c4ca4238a0b923820dcc509a6f75849b', 'sha1': '356a192b7913b04c54574d18c28d46e6395428ab', 'sha512': 'b109f3bbbc244eb82441917ed06d618b9008dd09b3befd1b5e07394c706a8bb980b1d7785e5976ec049b46df5f1326af5a2ea6d103fd07c95385ffab0cacbc86'}}}",
            [
                "b109f3bbbc244eb82441917ed06d618b9008dd09b3befd1b5e07394c706a8bb980b1d7785e5976ec049b46df5f1326af5a2ea6d103fd07c95385ffab0cacbc86"
            ],
        ),
        (
            "Command output: $ sha512sum implant.exe\ncf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e  implant.exe",
            [
                "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e"
            ],
        ),
        (
            "Incident response: found malware dropper with SHA512=B109F3BBBC244EB82441917ED06D618B9008DD09B3BEFD1B5E07394C706A8BB980B1D7785E5976EC049B46DF5F1326AF5A2EA6D103FD07C95385FFAB0CACBC86 on host",
            [
                "B109F3BBBC244EB82441917ED06D618B9008DD09B3BEFD1B5E07394C706A8BB980B1D7785E5976EC049B46DF5F1326AF5A2EA6D103FD07C95385FFAB0CACBC86"
            ],
        ),
        (
            "YARA match: rule Ransomware {strings: $a = 'ENCRYPTED' condition: $a} matched file with SHA512 hash DDAF35A193617ABACC417349AE20413112E6FA4E89A97EA20A9EEEE64B55D39A2192992A274FC1A836BA3C23A3FEEBBD454D4423643CE80E2A9AC94FA54CA49F",
            [
                "DDAF35A193617ABACC417349AE20413112E6FA4E89A97EA20A9EEEE64B55D39A2192992A274FC1A836BA3C23A3FEEBBD454D4423643CE80E2A9AC94FA54CA49F"
            ],
        ),
        (
            "Threat intel report mentions a multi-stage attack using files 204a8fc6dda82f0a0ced7eb905fd23c71d38b40262358d8d61f6b7177dbc4217c3dca86f72cfb424ea6c1775ebe4c40fd339265cc284278c08d8c7c29ab9a119 and 87aa7cdea5ef619d4ff0b4241a1d6cb02379f4e7d07789b442d8d80ef6166f1c2b474c8358cfb120fb03d18c744dccbf0191b3e6c386d275904721916b6209e0 as payloads",
            [
                "204a8fc6dda82f0a0ced7eb905fd23c71d38b40262358d8d61f6b7177dbc4217c3dca86f72cfb424ea6c1775ebe4c40fd339265cc284278c08d8c7c29ab9a119",
                "87aa7cdea5ef619d4ff0b4241a1d6cb02379f4e7d07789b442d8d80ef6166f1c2b474c8358cfb120fb03d18c744dccbf0191b3e6c386d275904721916b6209e0",
            ],
        ),
    ],
    ids=[
        "single_hash",
        "multiple_hashes",
        "no_hashes",
        "invalid_hash",
        "hash_in_json",
        "threat_hunting",
        "certificate_fingerprint",
        "defender_alert",
        "sandbox_report",
        "command_output",
        "incident_response",
        "yara_match",
        "threat_intel",
    ],
)
def test_extract_sha512_hashes(text: str, expected: list[str]) -> None:
    extracted = extract_sha512_hashes(text)
    assert sorted(extracted) == sorted(expected)


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "MAC address: 00:11:22:33:44:55 detected on network.",
            ["00:11:22:33:44:55"],
        ),
        (
            "Multiple formats: 00:11:22:33:44:55, AA-BB-CC-DD-EE-FF.",
            ["00:11:22:33:44:55", "AA:BB:CC:DD:EE:FF"],
        ),
        (
            "No MAC addresses here.",
            [],
        ),
        (
            "Invalid: 00:11:22:33:44, 00:11:22:33:44:55:66",
            [],
        ),
        (
            "MAC in data: {'mac': '00:1A:2B:3C:4D:5E', 'vendor': 'Example'}",
            ["00:1A:2B:3C:4D:5E"],
        ),
    ],
    ids=[
        "single_mac",
        "multiple_formats",
        "no_macs",
        "invalid_formats",
        "mac_in_json",
    ],
)
def test_extract_mac_addresses(text: str, expected: list[str]) -> None:
    extracted = extract_mac_addresses(text)
    assert sorted(extracted) == sorted(expected)


@pytest.mark.parametrize(
    "text,expected,normalize",
    [
        (
            "Contact us at support@example.com or sales@example.org.",
            ["support@example.com", "sales@example.org"],
            False,
        ),
        (
            "Invalid email test@.com and correct email info@example.net",
            ["info@example.net"],
            False,
        ),
        (
            "Email with subaddress john.doe+newsletter@example.com",
            ["john.doe+newsletter@example.com"],
            False,
        ),
        (
            "Email with subaddress john.doe+newsletter@example.com Another email jane.smith+promo@example.com",
            ["john.doe@example.com", "jane.smith@example.com"],
            True,
        ),
        (
            '{"email": "user@example.com", "more_info": {"contact": "admin@sub.example.com"}}',
            ["user@example.com", "admin@sub.example.com"],
            False,
        ),
        (
            '{"email": "user+info@example.com", "more_info": {"contact": "admin+test@sub.example.com"}}',
            ["user@example.com", "admin@sub.example.com"],
            True,
        ),
    ],
    ids=[
        "single_email",
        "invalid_and_valid_email",
        "subaddressed_email",
        "normalized_subaddressed_emails",
        "json_with_emails",
        "json_with_normalized_emails",
    ],
)
def test_extract_emails(text, expected, normalize):
    extracted_emails = extract_emails(text=text, normalize=normalize)
    assert sorted(extracted_emails) == sorted(expected)


@pytest.mark.parametrize(
    "email,expected",
    [
        ("user+info@example.com", "user@example.com"),
        ("user+info@sub.example.com", "user@sub.example.com"),
        ("user.name+tag@example.com", "user.name@example.com"),
        ("user+tag1+tag2@example.com", "user@example.com"),
        ("User.Name+Newsletter@Example.COM", "user.name@example.com"),
        ("user@example.com", "user@example.com"),
        ("user123+analytics@example.com", "user123@example.com"),
        ("user_name+test@example.com", "user_name@example.com"),
        ("firstname.lastname+role@company.co.uk", "firstname.lastname@company.co.uk"),
        ("user-name+marketing@example.org", "user-name@example.org"),
    ],
)
def test_normalize_email(email, expected):
    assert normalize_email(email) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        # Basic single IPv4 address in text
        ("IPv4 address 192.168.1.1 and some random text", ["192.168.1.1"]),
        # Multiple IPv4 addresses in text - common in log analysis
        (
            "IPv4 address 192.168.1.1 and another IPv4 address 10.0.0.1 in the mix",
            ["192.168.1.1", "10.0.0.1"],
        ),
        # No IP addresses - edge case
        ("No IP addresses here", []),
        # Invalid IPv4 addresses - should not be extracted
        (
            "Invalid IPv4 address 999.999.999.999 should not match. Another invalid 256.256.256.256 address",
            [],
        ),
        # Structured data - IP addresses in JSON alerts (common in SOAR platforms)
        (
            '{"alert": {"context": {"ip_address": "192.168.1.1", "description": "Suspicious activity detected"}}, "event": {"source": {"ip_address": "10.0.0.1", "port": 8080}, "destination": {"ip_address": "172.16.0.1", "port": 443}}}',
            ["192.168.1.1", "10.0.0.1", "172.16.0.1"],
        ),
        # Multiple addresses in comma-separated lists - common in threat intel feeds
        (
            "Multiple addresses: 192.168.1.1, 10.0.0.1. More addresses: 172.16.0.1, 192.168.0.1",
            ["192.168.1.1", "10.0.0.1", "172.16.0.1", "192.168.0.1"],
        ),
        # IP addresses with port numbers - common in connection logs
        (
            "Connection from 203.0.113.5:49123 to 198.51.100.12:443. Failed login attempt from 198.51.100.73:22 to internal server",
            ["203.0.113.5", "198.51.100.12", "198.51.100.73"],
        ),
        # IP addresses in firewall logs
        (
            "Apr 15 13:45:29 firewall kernel: INBOUND TCP 8.8.8.8:51812 -> 10.0.0.5:22 dropped. Apr 15 13:48:53 firewall kernel: OUTBOUND UDP 10.0.0.7:53 -> 1.1.1.1:53 allowed",
            ["8.8.8.8", "10.0.0.5", "10.0.0.7", "1.1.1.1"],
        ),
        # IP addresses in email headers (X-Forwarded-For, Received-From)
        (
            "Received: from mail-server (mail.example.com [192.0.2.1]) by smtp.gmail.com with ESMTPS. X-Forwarded-For: 203.0.113.195, 198.51.100.2, 192.0.2.45",
            ["192.0.2.1", "203.0.113.195", "198.51.100.2", "192.0.2.45"],
        ),
        # IP addresses in URL formats - common in phishing alerts
        (
            "Malicious URL detected: http://107.3.45.102/malware.exe. User visited suspicious site at https://103.244.36.182/login?user=admin",
            ["107.3.45.102", "103.244.36.182"],
        ),
        # IP addresses with CIDR notation in security rules
        (
            "Firewall rule added: DENY IN FROM 172.16.0.0/16 TO ANY. Allow traffic from trusted subnet 10.3.2.0/24 to dmz",
            ["172.16.0.0", "10.3.2.0"],
        ),
        # IP addresses in mixed content - mimics real-world alerts
        (
            "Alert ID 5823: Brute force attack detected from 45.76.123.45 (50 attempts). Investigation found connections to known C2 servers: 98.76.54.32, 23.45.67.89",
            ["45.76.123.45", "98.76.54.32", "23.45.67.89"],
        ),
    ],
    ids=[
        "single_ipv4",
        "multiple_ipv4",
        "no_ip_addresses",
        "invalid_ip_addresses",
        "json_with_ip_addresses",
        "comma_separated_ip_addresses",
        "ip_addresses_with_ports",
        "firewall_logs",
        "email_headers",
        "ip_addresses_in_urls",
        "ip_addresses_with_cidr",
        "mixed_content_alerts",
    ],
)
def test_extract_ipv4_addresses(text, expected):
    """Test the extraction of IPv4 addresses from various text formats.

    This test covers common scenarios in SOAR platforms where IPv4 addresses
    need to be extracted from different sources like log entries, alerts,
    JSON payloads, and structured data.
    """
    assert sorted(extract_ipv4_addresses(text=text)) == sorted(expected)


@pytest.mark.parametrize(
    "text,expected",
    [
        # Basic IPv6 address in full form
        (
            "IPv6 address 2001:0db8:85a3:0000:0000:8a2e:0370:7334 and some random text",
            ["2001:0db8:85a3:0000:0000:8a2e:0370:7334"],
        ),
        # IPv6 compressed form (with ::)
        ("Connection from 2001:db8::1 detected", ["2001:db8::1"]),
        # Multiple IPv6 addresses in security logs
        (
            "Failed login attempts from 2001:db8::ff00:42:8329 and fe80::1ff:fe23:4567:890a",
            ["2001:db8::ff00:42:8329", "fe80::1ff:fe23:4567:890a"],
        ),
        # IPv6 addresses in JSON alert payloads
        (
            '{"alert": {"src_ip": "2001:db8:85a3::8a2e:370:7334", "dst_ip": "2001:db8:3333:4444:5555:6666:7777:8888"}, "event": {"source": {"ip": "2620:0:2d0:200::7"}, "destination": {"ip": "2001:0:3238:DFE1:63::FEFB"}}}',
            [
                "2001:db8:85a3::8a2e:370:7334",
                "2001:db8:3333:4444:5555:6666:7777:8888",
                "2620:0:2d0:200::7",
                "2001:0:3238:DFE1:63::FEFB",
            ],
        ),
        # IPv6 addresses with port numbers in brackets
        (
            "Suspicious connection detected from [2001:db8::1]:8080 to [2001:db8:1::ab9:C0A8:102]:443",
            ["2001:db8::1", "2001:db8:1::ab9:C0A8:102"],
        ),
        # IPv6 addresses in firewall logs
        (
            "Apr 15 13:45:29 firewall kernel: INBOUND TCP [2001:db8:1::1]:51812 -> [2001:db8:2::2]:22 dropped",
            ["2001:db8:1::1", "2001:db8:2::2"],
        ),
        # IPv6 addresses in URLs
        (
            "Malicious content detected at https://[2001:db8::bad:1]/malware.exe",
            ["2001:db8::bad:1"],
        ),
        # Various IPv6 address types in network alerts
        (
            "Link-local: fe80::1, Unique local: fd00::1, Global unicast: 2001:db8::1, Multicast: ff02::1",
            ["fe80::1", "fd00::1", "2001:db8::1", "ff02::1"],
        ),
        # IPv6 transition mechanisms
        (
            "Teredo tunneling detected from 2001:0:5ef5:79fb:0:59a:a95e:3a46 to the internet",
            ["2001:0:5ef5:79fb:0:59a:a95e:3a46"],
        ),
        # No IPv6 addresses
        ("No IPv6 addresses in this text, only IPv4 like 192.168.1.1", []),
    ],
    ids=[
        "full_ipv6",
        "compressed_ipv6",
        "multiple_ipv6",
        "json_alerts",
        "ipv6_with_ports",
        "firewall_logs",
        "ipv6_in_url",
        "ipv6_address_types",
        "ipv6_tunneling",
        "no_ipv6",
    ],
)
def test_extract_ipv6_addresses(text, expected):
    assert sorted(extract_ipv6_addresses(text=text)) == sorted(expected)


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "Visit our website at https://example.com for more info.",
            ["https://example.com"],
        ),
        (
            "This url was secured: https://secure.url/nX-BpUKr17mePOHRS5_IUlHEPW//https%3A%2F%2Fmyurl.com",
            ["https://secure.url/nX-BpUKr17mePOHRS5_IUlHEPW//https%3A%2F%2Fmyurl.com"],
        ),
        (
            "<p>Click on this link: <a href='https://www.exemple.com/SUB' target='_blank'>https://www.exemple.com/SUB</a></p>",
            ["https://www.exemple.com/SUB"],
        ),
        (
            "Check out https://example.com/path and http://example.org/another-path for details.",
            ["https://example.com/path", "http://example.org/another-path"],
        ),
        (
            "Invalid URL test http://.com and correct URL https://example.net/path",
            ["https://example.net/path"],
        ),
        (
            "Multiple URLs: https://example.com/path1, http://example.org/path2, and https://sub.example.net/path3.",
            [
                "https://example.com/path1",
                "http://example.org/path2",
                "https://sub.example.net/path3",
            ],
        ),
        (
            '{"url": "https://example.com/path", "more_info": {"link": "http://sub.example.com/another-path"}}',
            ["https://example.com/path", "http://sub.example.com/another-path"],
        ),
        (
            "Text with no URLs should return an empty list.",
            [],
        ),
        (
            "Check out this search result: https://example.com/search?q=malware&source=web&safe=on",
            ["https://example.com/search?q=malware&source=web&safe=on"],
        ),
        (
            "URL with fragment identifier: https://example.com/article#section-2",
            ["https://example.com/article#section-2"],
        ),
        (
            "URL with special characters: https://example.com/path/with/special/%20characters%21",
            ["https://example.com/path/with/special/%20characters%21"],
        ),
        (
            "URL with credentials: https://user:password@example.com/admin",
            ["https://user:password@example.com/admin"],
        ),
        (
            "URL with port number: http://example.com:8080/api/v1",
            ["http://example.com:8080/api/v1"],
        ),
        (
            "Phishing URL that uses lookalike domain: http://examp1e.com/login (note the '1' instead of 'l')",
            ["http://examp1e.com/login"],
        ),
        (
            "Multiple URLs in JSON array: {'urls': ['https://malicious.com/payload', 'http://benign.org/file']}",
            ["https://malicious.com/payload", "http://benign.org/file"],
        ),
        (
            "Data URL: data:text/html;base64,SGVsbG8gV29ybGQh and normal URL https://example.com",
            ["https://example.com"],
        ),
        (
            "URL in email content: The attacker sent a message containing http://malware.example.net/download.php?id=123456",
            ["http://malware.example.net/download.php?id=123456"],
        ),
        (
            "URL with non-ASCII characters: https://例子.测试/path",
            ["https://例子.测试/path"],
        ),
        (
            "Common obfuscation trick: hxxps://evil.example[.]com/malware.exe",
            [],
        ),
        (
            "Deep nested JSON: {'alert': {'data': {'indicators': {'urls': ['https://c2server.com/beacon', 'https://exfil.example.net/drop']}}}}",
            ["https://c2server.com/beacon", "https://exfil.example.net/drop"],
        ),
        (
            "FTP server available at ftp://files.example.com/public/docs/",
            ["ftp://files.example.com/public/docs"],
        ),
        (
            "Secure FTP endpoints: sftp://secure.example.org:22/upload and ftps://files.example.net/secure/",
            ["sftp://secure.example.org:22/upload", "ftps://files.example.net/secure"],
        ),
        (
            "TCP service endpoint: tcp://streaming.example.com:1234 for real-time data",
            ["tcp://streaming.example.com:1234"],
        ),
        (
            "UDP service available at udp://game.example.org:5678 for multiplayer gaming",
            ["udp://game.example.org:5678"],
        ),
        (
            "Multiple protocol URLs: ftp://files.example.com, tcp://service.example.org:8000, and udp://broadcast.example.net:9000",
            [
                "ftp://files.example.com",
                "tcp://service.example.org:8000",
                "udp://broadcast.example.net:9000",
            ],
        ),
        (
            "JSON with various protocols: {'endpoints': {'ftp': 'ftp://storage.example.com:21', 'streaming': 'tcp://stream.example.org:1234'}}",
            ["ftp://storage.example.com:21", "tcp://stream.example.org:1234"],
        ),
    ],
    ids=[
        "single_url_string",
        "single_url_with_encoded_path",
        "single_url_in_html",
        "two_urls_in_list",
        "filter_invalid_url",
        "multiple_urls_in_text",
        "urls_in_json_string",
        "empty_result",
        "url_with_query_parameters",
        "url_with_fragment",
        "url_with_percent_encoding",
        "url_with_credentials",
        "url_with_port",
        "phishing_lookalike_domain",
        "urls_in_json_array",
        "data_url_not_matched",
        "url_in_email_content",
        "url_with_non_ascii_characters",
        "obfuscated_url_not_matched",
        "urls_in_deeply_nested_json",
        "ftp_url",
        "secure_ftp_urls",
        "tcp_url_with_port",
        "udp_url_with_port",
        "multiple_protocol_urls",
        "json_with_various_protocols",
    ],
)
def test_extract_urls(text, expected):
    extracted_urls = extract_urls(text=text)
    assert sorted(extracted_urls) == sorted(expected)


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "Mixed protocols: https://example.com, ftp://files.example.org, http://test.net",
            ["https://example.com", "http://test.net"],
        ),
        (
            "Only HTTP/HTTPS: http://example.com and https://secure.example.org/path?query=123",
            ["http://example.com", "https://secure.example.org/path?query=123"],
        ),
        (
            "Only non-HTTP protocols: ftp://files.example.com, sftp://secure.example.org:22, tcp://stream.example.net:1234",
            [],
        ),
        (
            "URL with credentials and port: https://user:pass@example.com:8443/admin and tcp://admin:secret@server.net:9000",
            ["https://user:pass@example.com:8443/admin"],
        ),
        (
            "URLs in JSON structure: {'web': 'https://api.example.com', 'file': 'ftp://download.example.org', 'backup': 'http://backup.example.net'}",
            ["https://api.example.com", "http://backup.example.net"],
        ),
        (
            "URLs with special characters: https://例子.测试/path and ftp://例子.测试/download",
            ["https://例子.测试/path"],
        ),
        (
            "No URLs at all",
            [],
        ),
        (
            "URL with query parameters: https://example.com/search?q=test&page=1 and udp://stream.example.com:5000",
            ["https://example.com/search?q=test&page=1"],
        ),
        (
            "URLs in HTML: <a href='https://example.com'>Link</a> and <a href='ftp://files.example.org'>Files</a>",
            ["https://example.com"],
        ),
    ],
    ids=[
        "mixed_protocols",
        "only_http_https",
        "only_non_http",
        "credentials_and_port",
        "urls_in_json",
        "urls_with_special_chars",
        "no_urls",
        "url_with_query_params",
        "urls_in_html",
    ],
)
def test_extract_urls_http_only(text, expected):
    """Test that extract_urls with http_only=True only returns HTTP and HTTPS URLs."""
    extracted_urls = extract_urls(text=text, http_only=True)
    assert sorted(extracted_urls) == sorted(expected)


def test_extract_domains_exception():
    """Test that extract_domains properly handles validation errors."""
    mixed_input = """
    Valid: example.com, sub.example.org
    Invalid: not_a_domain, .invalid, example..com, -example.com
    """
    result = extract_domains(mixed_input)
    assert sorted(result) == ["example.com", "sub.example.org"]


def test_extract_urls_exception():
    """Test that extract_urls properly handles validation errors."""
    mixed_input = """
    Valid: https://example.com, http://sub.example.org/path, ftp://example.com, http://example.com
    Invalid: http://.com, https://invalid, http://example.com:abc
    """
    result = extract_urls(mixed_input)
    assert sorted(result) == [
        "ftp://example.com",
        "http://example.com",
        "http://sub.example.org/path",
        "https://example.com",
    ]


def test_extract_ipv4_addresses_exception():
    """Test that extract_ipv4_addresses properly handles validation errors."""
    mixed_input = """
    Valid: 192.168.1.1, 10.0.0.1, 8.8.8.8
    Invalid: 256.256.256.256, 192.168.1, 192.168.1.1.1, 999.999.999.999
    """
    result = extract_ipv4_addresses(mixed_input)
    assert sorted(result) == ["10.0.0.1", "192.168.1.1", "8.8.8.8"]


def test_extract_ipv6_addresses_exception():
    """Test that extract_ipv6_addresses properly handles validation errors."""
    # Provide input with valid and invalid examples
    test_input = "Valid IPv6: 2001:db8::1 and 2001:0db8:85a3:0000:0000:8a2e:0370:7334\nInvalid: xyz"
    result = extract_ipv6_addresses(test_input)
    expected = ["2001:db8::1", "2001:0db8:85a3:0000:0000:8a2e:0370:7334"]
    assert sorted(result) == sorted(expected)


def test_extract_mac_addresses_exception():
    """Test that extract_mac_addresses properly handles validation errors."""
    mixed_input = """
    Valid: 00:11:22:33:44:55, AA-BB-CC-DD-EE-FF
    Invalid: 00:11:22:33:44, 00:11:22:33:44:55:66, GG:HH:II:JJ:KK:LL
    """
    result = extract_mac_addresses(mixed_input)
    assert sorted(result) == ["00:11:22:33:44:55", "AA:BB:CC:DD:EE:FF"]


def test_extract_emails_exception():
    """Test that extract_emails properly handles validation errors."""
    mixed_input = """
    Valid: user@example.com, first.last@sub.domain.com
    Invalid: user@, @example.com, user@domain, plaintext
    """
    result = extract_emails(mixed_input)
    assert sorted(result) == ["first.last@sub.domain.com", "user@example.com"]


### DEFANGED IOC EXTRACTORS
