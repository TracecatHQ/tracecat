import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import CaseCreate
from tracecat.cases.records.service import CaseEntitiesService
from tracecat.cases.service import CasesService
from tracecat.entities.enums import RelationType
from tracecat.entities.models import RelationDefinitionCreate
from tracecat.entities.service import CustomEntitiesService
from tracecat.entities.types import FieldType
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def entities_admin_service(
    session: AsyncSession, svc_admin_role: Role
) -> CustomEntitiesService:
    return CustomEntitiesService(session=session, role=svc_admin_role)


# Note: Dedicated admin and case entity services are used in tests.
# The generic `entities_service` fixture was unused and has been removed for clarity.


@pytest.fixture
async def case_entities_service(
    session: AsyncSession, svc_role: Role
) -> CaseEntitiesService:
    return CaseEntitiesService(session=session, role=svc_role)


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:
    return CasesService(session=session, role=svc_role)


async def _prepare_phishing_email(
    svc: CustomEntitiesService,
) -> tuple[uuid.UUID, dict[str, Any], dict[str, Any]]:
    """Entity: phishing_email with sender, receiver, cc, eml_base64."""
    entity = await svc.create_entity(
        name="phishing_email", display_name="Phishing Email"
    )
    await svc.create_field(entity.id, "sender", FieldType.TEXT, "Sender")
    await svc.create_field(entity.id, "receiver", FieldType.TEXT, "Receiver")
    await svc.create_field(entity.id, "cc", FieldType.ARRAY_TEXT, "CC")
    await svc.create_field(entity.id, "eml_base64", FieldType.TEXT, "EML Base64")

    create_data = {
        "sender": "attacker@phishing-domain.com",
        "receiver": "victim@company.com",
        "cc": ["vip@company.com", "soc@company.com"],
        "eml_base64": "TUVTU0FHRS1JRC1YWFhYLUJhc2U2NA==" * 10,
    }
    update_data = {"receiver": "newvictim@company.com", "cc": ["ir@company.com"]}
    return entity.id, create_data, update_data


async def _prepare_vip(
    svc: CustomEntitiesService,
) -> tuple[uuid.UUID, dict[str, Any], dict[str, Any]]:
    """Entity: vip with title, first_name, last_name, email, years_in_company."""
    entity = await svc.create_entity(name="vip", display_name="VIP")
    await svc.create_field(
        entity.id, "title", FieldType.SELECT, "Title", enum_options=["Mr", "Ms", "Dr"]
    )
    await svc.create_field(entity.id, "first_name", FieldType.TEXT, "First Name")
    await svc.create_field(entity.id, "last_name", FieldType.TEXT, "Last Name")
    await svc.create_field(entity.id, "email", FieldType.TEXT, "Email")
    await svc.create_field(entity.id, "years_in_company", FieldType.INTEGER, "Years")
    await svc.create_field(entity.id, "active", FieldType.BOOL, "Active")

    create_data = {
        "title": "Dr",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@company.com",
        "years_in_company": 5,
        "active": True,
    }
    update_data = {"years_in_company": 6, "title": "Ms", "active": False}
    return entity.id, create_data, update_data


async def _prepare_all_field_types(
    svc: CustomEntitiesService,
) -> tuple[uuid.UUID, dict[str, Any], dict[str, Any]]:
    """Entity that exercises all supported field types including relations."""
    # Targets for relations
    dept = await svc.create_entity(name="department", display_name="Department")
    await svc.create_field(dept.id, "name", FieldType.TEXT, "Name")
    dept_rec = await svc.create_record(dept.id, {"name": "Engineering"})

    project = await svc.create_entity(name="project", display_name="Project")
    await svc.create_field(project.id, "code", FieldType.TEXT, "Code")
    proj_a = await svc.create_record(project.id, {"code": "PJT-A"})
    proj_b = await svc.create_record(project.id, {"code": "PJT-B"})

    # Entity covering all types
    entity = await svc.create_entity(name="all_types", display_name="All Types")

    # Primitive
    await svc.create_field(entity.id, "integer_value", FieldType.INTEGER, "Integer")
    await svc.create_field(entity.id, "number_value", FieldType.NUMBER, "Number")
    await svc.create_field(entity.id, "text_value", FieldType.TEXT, "Text")
    await svc.create_field(entity.id, "boolean_value", FieldType.BOOL, "Boolean")
    # Date/time
    await svc.create_field(entity.id, "datetime_value", FieldType.DATETIME, "Datetime")
    await svc.create_field(entity.id, "date_value", FieldType.DATE, "Date")
    # Arrays
    await svc.create_field(entity.id, "array_text", FieldType.ARRAY_TEXT, "Array Text")
    await svc.create_field(
        entity.id, "array_integer", FieldType.ARRAY_INTEGER, "Array Integer"
    )
    await svc.create_field(
        entity.id, "array_number", FieldType.ARRAY_NUMBER, "Array Number"
    )
    # Selects
    await svc.create_field(
        entity.id,
        "select_value",
        FieldType.SELECT,
        "Select",
        enum_options=["A", "B", "C"],
    )
    await svc.create_field(
        entity.id,
        "multi_select",
        FieldType.MULTI_SELECT,
        "Multi Select",
        enum_options=["x", "y", "z"],
    )
    # Relations
    await svc.create_relation(
        entity.id,
        RelationDefinitionCreate(
            source_key="department",
            display_name="Department",
            relation_type=RelationType.ONE_TO_ONE,
            target_entity_id=dept.id,
        ),
    )
    await svc.create_relation(
        entity.id,
        RelationDefinitionCreate(
            source_key="projects",
            display_name="Projects",
            relation_type=RelationType.ONE_TO_MANY,
            target_entity_id=project.id,
        ),
    )

    create_data = {
        "integer_value": 123,
        "number_value": 3.14,
        "text_value": "hello",
        "boolean_value": True,
        "datetime_value": datetime.now(UTC).isoformat(),
        "date_value": date.today().isoformat(),
        "array_text": ["a", "b"],
        "array_integer": [1, 2, 3],
        "array_number": [1.5, 2.5],
        "select_value": "B",
        "multi_select": ["x", "z"],
        "department": str(dept_rec.id),
        "projects": [str(proj_a.id), str(proj_b.id)],
    }
    update_data = {
        "integer_value": 456,
        "text_value": "world",
        "multi_select": ["y"],
    }
    return entity.id, create_data, update_data


@pytest.mark.anyio
async def test_case_entities_with_many_to_one_and_many_to_many(
    case_entities_service: CaseEntitiesService,
    entities_admin_service: CustomEntitiesService,
    cases_service: CasesService,
) -> None:
    """Ensure case list resolves many_to_one as object and many_to_many as list."""
    # Create case
    case = await cases_service.create_case(
        CaseCreate(
            summary="Test case for entities",
            description="",
            status=CaseStatus.NEW,
            severity=CaseSeverity.MEDIUM,
            priority=CasePriority.MEDIUM,
        )
    )

    # Setup entities
    parent = await entities_admin_service.create_entity(
        name="ce_parent", display_name="Parent"
    )
    await entities_admin_service.create_field(parent.id, "name", FieldType.TEXT, "Name")
    p1 = await entities_admin_service.create_record(parent.id, {"name": "P1"})

    tag = await entities_admin_service.create_entity(name="ce_tag", display_name="Tag")
    await entities_admin_service.create_field(tag.id, "name", FieldType.TEXT, "Name")
    t1 = await entities_admin_service.create_record(tag.id, {"name": "T1"})
    t2 = await entities_admin_service.create_record(tag.id, {"name": "T2"})

    child = await entities_admin_service.create_entity(
        name="ce_child", display_name="Child"
    )
    await entities_admin_service.create_field(
        child.id, "title", FieldType.TEXT, "Title"
    )

    await entities_admin_service.create_relation(
        child.id,
        RelationDefinitionCreate(
            source_key="parent",
            display_name="Parent",
            relation_type=RelationType.MANY_TO_ONE,
            target_entity_id=parent.id,
        ),
    )
    await entities_admin_service.create_relation(
        child.id,
        RelationDefinitionCreate(
            source_key="tags",
            display_name="Tags",
            relation_type=RelationType.MANY_TO_MANY,
            target_entity_id=tag.id,
        ),
    )

    # Create child record and link to case
    await case_entities_service.create_record(
        case_id=case.id,
        entity_id=child.id,
        entity_data={
            "title": "Child A",
            "parent": str(p1.id),
            "tags": [str(t1.id), str(t2.id)],
        },
    )

    # List records for case and inspect resolved relations
    results = await case_entities_service.list_records(case.id)
    assert len(results) == 1
    rec = results[0].record
    assert rec is not None
    assert "parent" in rec.relation_fields and "tags" in rec.relation_fields
    assert isinstance(rec.field_data.get("parent"), dict)
    assert isinstance(rec.field_data.get("tags"), list)


async def _prepare_security_alert(
    svc: CustomEntitiesService,
) -> tuple[uuid.UUID, dict[str, Any], dict[str, Any]]:
    """Entity: security_alert for SIEM/EDR alerts."""
    entity = await svc.create_entity(
        name="security_alert", display_name="Security Alert"
    )

    await svc.create_field(entity.id, "alert_id", FieldType.TEXT, "Alert ID")
    await svc.create_field(
        entity.id,
        "severity",
        FieldType.SELECT,
        "Severity",
        enum_options=["critical", "high", "medium", "low", "info"],
    )
    await svc.create_field(
        entity.id,
        "status",
        FieldType.SELECT,
        "Status",
        enum_options=["new", "triaged", "investigating", "resolved", "false_positive"],
    )
    await svc.create_field(
        entity.id,
        "source_system",
        FieldType.SELECT,
        "Source System",
        enum_options=["crowdstrike", "sentinel_one", "splunk", "elastic"],
    )
    await svc.create_field(entity.id, "rule_name", FieldType.TEXT, "Rule Name")
    await svc.create_field(
        entity.id,
        "tactics",
        FieldType.MULTI_SELECT,
        "MITRE Tactics",
        enum_options=[
            "initial_access",
            "execution",
            "persistence",
            "privilege_escalation",
            "defense_evasion",
            "credential_access",
            "discovery",
            "lateral_movement",
            "collection",
            "command_and_control",
            "exfiltration",
            "impact",
        ],
    )
    await svc.create_field(entity.id, "techniques", FieldType.ARRAY_TEXT, "Techniques")
    await svc.create_field(
        entity.id, "affected_hosts", FieldType.ARRAY_TEXT, "Affected Hosts"
    )
    await svc.create_field(entity.id, "first_seen", FieldType.DATETIME, "First Seen")
    await svc.create_field(entity.id, "last_seen", FieldType.DATETIME, "Last Seen")
    await svc.create_field(entity.id, "event_count", FieldType.INTEGER, "Event Count")
    await svc.create_field(
        entity.id, "confidence_score", FieldType.NUMBER, "Confidence Score"
    )

    now = datetime.now(UTC)
    create_data = {
        "alert_id": "ALT-2024-001234",
        "severity": "high",
        "status": "new",
        "source_system": "crowdstrike",
        "rule_name": "Suspicious PowerShell Execution",
        "tactics": ["execution", "defense_evasion"],
        "techniques": ["T1059.001", "T1027"],
        "affected_hosts": ["DESKTOP-ABC123", "SERVER-XYZ789"],
        "first_seen": (now - timedelta(hours=2)).isoformat(),
        "last_seen": now.isoformat(),
        "event_count": 47,
        "confidence_score": 0.85,
    }
    update_data = {
        "status": "investigating",
        "event_count": 52,
        "affected_hosts": ["DESKTOP-ABC123", "SERVER-XYZ789", "LAPTOP-DEF456"],
    }
    return entity.id, create_data, update_data


async def _prepare_vulnerability(
    svc: CustomEntitiesService,
) -> tuple[uuid.UUID, dict[str, Any], dict[str, Any]]:
    """Entity: vulnerability for vulnerability management."""
    entity = await svc.create_entity(name="vulnerability", display_name="Vulnerability")

    await svc.create_field(entity.id, "cve_id", FieldType.TEXT, "CVE ID")
    await svc.create_field(entity.id, "cvss_score", FieldType.NUMBER, "CVSS Score")
    await svc.create_field(entity.id, "cvss_vector", FieldType.TEXT, "CVSS Vector")
    await svc.create_field(
        entity.id,
        "severity",
        FieldType.SELECT,
        "Severity",
        enum_options=["critical", "high", "medium", "low"],
    )
    await svc.create_field(
        entity.id, "affected_assets", FieldType.ARRAY_TEXT, "Affected Assets"
    )
    await svc.create_field(
        entity.id, "affected_software", FieldType.TEXT, "Affected Software"
    )
    await svc.create_field(
        entity.id, "patch_available", FieldType.BOOL, "Patch Available"
    )
    await svc.create_field(entity.id, "patch_url", FieldType.TEXT, "Patch URL")
    await svc.create_field(
        entity.id, "exploited_in_wild", FieldType.BOOL, "Exploited in Wild"
    )
    await svc.create_field(entity.id, "epss_score", FieldType.NUMBER, "EPSS Score")
    await svc.create_field(
        entity.id, "discovered_date", FieldType.DATE, "Discovered Date"
    )
    await svc.create_field(
        entity.id, "patch_deadline", FieldType.DATE, "Patch Deadline"
    )
    await svc.create_field(
        entity.id,
        "scan_source",
        FieldType.SELECT,
        "Scan Source",
        enum_options=["qualys", "nessus", "rapid7", "crowdstrike"],
    )
    await svc.create_field(
        entity.id,
        "remediation_status",
        FieldType.SELECT,
        "Remediation Status",
        enum_options=["open", "patching", "mitigated", "resolved", "accepted"],
    )

    create_data = {
        "cve_id": "CVE-2024-12345",
        "cvss_score": 9.8,
        "cvss_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "severity": "critical",
        "affected_assets": ["webserver01.company.com", "webserver02.company.com"],
        "affected_software": "Apache Log4j 2.14.1",
        "patch_available": True,
        "patch_url": "https://logging.apache.org/log4j/2.x/security.html",
        "exploited_in_wild": True,
        "epss_score": 0.97654,
        "discovered_date": date.today().isoformat(),
        "patch_deadline": (date.today() + timedelta(days=7)).isoformat(),
        "scan_source": "qualys",
        "remediation_status": "open",
    }
    update_data = {
        "remediation_status": "patching",
        "affected_assets": ["webserver01.company.com"],
    }
    return entity.id, create_data, update_data


async def _prepare_incident_timeline(
    svc: CustomEntitiesService,
) -> tuple[uuid.UUID, dict[str, Any], dict[str, Any]]:
    """Entity: incident_timeline for incident response timeline events."""
    entity = await svc.create_entity(
        name="incident_timeline", display_name="Incident Timeline"
    )

    await svc.create_field(
        entity.id,
        "event_type",
        FieldType.SELECT,
        "Event Type",
        enum_options=[
            "detection",
            "containment",
            "eradication",
            "recovery",
            "communication",
            "escalation",
        ],
    )
    await svc.create_field(entity.id, "timestamp", FieldType.DATETIME, "Timestamp")
    await svc.create_field(entity.id, "description", FieldType.TEXT, "Description")
    await svc.create_field(entity.id, "performed_by", FieldType.TEXT, "Performed By")
    await svc.create_field(
        entity.id, "affected_systems", FieldType.ARRAY_TEXT, "Affected Systems"
    )
    await svc.create_field(
        entity.id, "iocs_observed", FieldType.ARRAY_TEXT, "IOCs Observed"
    )
    await svc.create_field(
        entity.id, "evidence_collected", FieldType.ARRAY_TEXT, "Evidence Collected"
    )
    await svc.create_field(
        entity.id, "automated_action", FieldType.BOOL, "Automated Action"
    )
    await svc.create_field(entity.id, "playbook_step", FieldType.TEXT, "Playbook Step")
    await svc.create_field(
        entity.id, "duration_minutes", FieldType.INTEGER, "Duration (minutes)"
    )

    now = datetime.now(UTC)
    create_data = {
        "event_type": "detection",
        "timestamp": now.isoformat(),
        "description": "Suspicious network activity detected from internal host",
        "performed_by": "SIEM Alert",
        "affected_systems": ["10.0.1.50", "database.internal"],
        "iocs_observed": ["192.168.1.100", "evil.malware-c2.com"],
        "evidence_collected": ["network_capture_001.pcap", "memory_dump_001.dmp"],
        "automated_action": True,
        "playbook_step": "IR-PLAYBOOK-001-STEP-1",
        "duration_minutes": 5,
    }
    update_data = {
        "event_type": "containment",
        "duration_minutes": 15,
        "automated_action": False,
    }
    return entity.id, create_data, update_data


async def _prepare_threat_intel(
    svc: CustomEntitiesService,
) -> tuple[uuid.UUID, dict[str, Any], dict[str, Any]]:
    """Entity: threat_intel for IOC tracking."""
    entity = await svc.create_entity(name="threat_intel", display_name="Threat Intel")

    await svc.create_field(
        entity.id,
        "ioc_type",
        FieldType.SELECT,
        "IOC Type",
        enum_options=[
            "ip",
            "domain",
            "url",
            "hash_md5",
            "hash_sha1",
            "hash_sha256",
            "email",
            "cve",
        ],
    )
    await svc.create_field(entity.id, "ioc_value", FieldType.TEXT, "IOC Value")
    await svc.create_field(entity.id, "threat_actor", FieldType.TEXT, "Threat Actor")
    await svc.create_field(entity.id, "campaign", FieldType.TEXT, "Campaign")
    await svc.create_field(
        entity.id, "malware_family", FieldType.TEXT, "Malware Family"
    )
    await svc.create_field(
        entity.id,
        "confidence",
        FieldType.SELECT,
        "Confidence",
        enum_options=["high", "medium", "low"],
    )
    await svc.create_field(
        entity.id,
        "tlp_level",
        FieldType.SELECT,
        "TLP Level",
        enum_options=["red", "amber", "green", "white"],
    )
    await svc.create_field(
        entity.id,
        "tags",
        FieldType.MULTI_SELECT,
        "Tags",
        enum_options=[
            "phishing",
            "ransomware",
            "apt",
            "botnet",
            "trojan",
            "backdoor",
        ],
    )
    await svc.create_field(
        entity.id, "source_feeds", FieldType.ARRAY_TEXT, "Source Feeds"
    )
    await svc.create_field(entity.id, "first_seen", FieldType.DATETIME, "First Seen")
    await svc.create_field(entity.id, "last_seen", FieldType.DATETIME, "Last Seen")
    await svc.create_field(entity.id, "active", FieldType.BOOL, "Active")
    await svc.create_field(
        entity.id, "false_positive", FieldType.BOOL, "False Positive"
    )

    now = datetime.now(UTC)
    create_data = {
        "ioc_type": "domain",
        "ioc_value": "malicious-phishing-site.com",
        "threat_actor": "APT28",
        "campaign": "Operation PhishNet",
        "malware_family": "Emotet",
        "confidence": "high",
        "tlp_level": "amber",
        "tags": ["phishing", "apt"],
        "source_feeds": ["AlienVault OTX", "Recorded Future", "CrowdStrike Intel"],
        "first_seen": (now - timedelta(days=30)).isoformat(),
        "last_seen": now.isoformat(),
        "active": True,
        "false_positive": False,
    }
    update_data = {
        "active": False,
        "confidence": "medium",
        "tags": ["phishing", "apt", "ransomware"],
    }
    return entity.id, create_data, update_data


async def _prepare_compliance_finding(
    svc: CustomEntitiesService,
) -> tuple[uuid.UUID, dict[str, Any], dict[str, Any]]:
    """Entity: compliance_finding for audit findings."""
    entity = await svc.create_entity(
        name="compliance_finding", display_name="Compliance Finding"
    )

    await svc.create_field(
        entity.id,
        "framework",
        FieldType.SELECT,
        "Framework",
        enum_options=["nist", "iso27001", "soc2", "pci_dss", "hipaa", "gdpr"],
    )
    await svc.create_field(entity.id, "control_id", FieldType.TEXT, "Control ID")
    await svc.create_field(
        entity.id, "control_description", FieldType.TEXT, "Control Description"
    )
    await svc.create_field(
        entity.id,
        "finding_type",
        FieldType.SELECT,
        "Finding Type",
        enum_options=["gap", "non_conformity", "observation", "opportunity"],
    )
    await svc.create_field(
        entity.id,
        "severity",
        FieldType.SELECT,
        "Severity",
        enum_options=["critical", "major", "minor"],
    )
    await svc.create_field(
        entity.id, "affected_systems", FieldType.ARRAY_TEXT, "Affected Systems"
    )
    await svc.create_field(entity.id, "evidence", FieldType.ARRAY_TEXT, "Evidence")
    await svc.create_field(
        entity.id, "remediation_plan", FieldType.TEXT, "Remediation Plan"
    )
    await svc.create_field(entity.id, "due_date", FieldType.DATE, "Due Date")
    await svc.create_field(entity.id, "owner", FieldType.TEXT, "Owner")
    await svc.create_field(
        entity.id,
        "status",
        FieldType.SELECT,
        "Status",
        enum_options=["open", "in_progress", "remediated", "verified", "accepted"],
    )
    await svc.create_field(
        entity.id, "cost_estimate", FieldType.NUMBER, "Cost Estimate"
    )
    await svc.create_field(entity.id, "audit_date", FieldType.DATE, "Audit Date")

    create_data = {
        "framework": "nist",
        "control_id": "AC-2",
        "control_description": "Account Management",
        "finding_type": "gap",
        "severity": "major",
        "affected_systems": ["Active Directory", "AWS IAM", "Azure AD"],
        "evidence": ["audit_report_2024.pdf", "screenshots/ad_config.png"],
        "remediation_plan": "Implement automated account lifecycle management",
        "due_date": (date.today() + timedelta(days=90)).isoformat(),
        "owner": "IT Security Team",
        "status": "open",
        "cost_estimate": 25000.00,
        "audit_date": date.today().isoformat(),
    }
    update_data = {
        "status": "in_progress",
        "cost_estimate": 30000.00,
    }
    return entity.id, create_data, update_data


async def _prepare_scenario(
    name: str, svc: CustomEntitiesService
) -> tuple[uuid.UUID, dict[str, Any], dict[str, Any]]:
    """Route to the appropriate scenario preparation function."""
    scenarios = {
        "phishing_email": _prepare_phishing_email,
        "vip": _prepare_vip,
        "all_fields": _prepare_all_field_types,
        "security_alert": _prepare_security_alert,
        "vulnerability": _prepare_vulnerability,
        "incident_timeline": _prepare_incident_timeline,
        "threat_intel": _prepare_threat_intel,
        "compliance_finding": _prepare_compliance_finding,
    }

    if name not in scenarios:
        raise ValueError(f"Unknown scenario: {name}")

    return await scenarios[name](svc)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "scenario",
    [
        "phishing_email",
        "vip",
        "all_fields",
        "security_alert",
        "vulnerability",
        "incident_timeline",
        "threat_intel",
        "compliance_finding",
    ],
)
class TestCaseEntitiesService:
    async def test_add_update_remove_delete_flow(
        self,
        scenario: str,
        cases_service: CasesService,
        case_entities_service: CaseEntitiesService,
        entities_admin_service: CustomEntitiesService,
    ) -> None:
        """Test the full lifecycle of case entity records."""
        # Create a case
        case = await cases_service.create_case(
            CaseCreate(
                summary=f"Security incident: {scenario}",
                description=f"Integration test for {scenario} entity",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.MEDIUM,
            )
        )

        # Prepare entity, record data, and updates
        entity_id, record_data, update_data = await _prepare_scenario(
            scenario, entities_admin_service
        )

        # Create and link new record to the case
        link = await case_entities_service.create_record(
            case_id=case.id,
            entity_id=entity_id,
            entity_data=record_data,
        )
        assert link.record is not None
        assert link.record.id == link.record_id
        assert link.case_id == case.id
        assert link.entity_id == entity_id

        # List records and verify field data is present
        results = await case_entities_service.list_records(case.id)
        found_link = None
        found_entity = None
        found_record = None

        for item in results:
            if item.record_id == link.record_id:
                found_link = item
                found_entity = item.entity
                found_record = item.record
                break

        assert found_link is not None
        assert found_entity is not None
        assert found_record is not None

        # Verify entity metadata
        assert found_entity.id == entity_id
        assert found_entity.display_name is not None

        # Verify keys from input exist in field_data
        # Note: Relation fields are stored via relation links and not in field_data
        for key in record_data.keys():
            if key not in {"projects", "department"}:  # relations stored separately
                assert key in found_record.field_data

        # Update record via case service
        updated = await case_entities_service.update_record(
            case.id, link.record_id, update_data
        )
        for key in update_data:
            if key not in {"projects"}:
                # Check the field was updated
                assert key in updated.field_data

        # Remove association (record preserved)
        await case_entities_service.remove_record(case.id, link.id)
        results_after_remove = await case_entities_service.list_records(case.id)
        record_ids_after_remove = [item.record_id for item in results_after_remove]
        assert link.record_id not in record_ids_after_remove

        # Confirm record still exists
        still_exists = await entities_admin_service.get_record(link.record_id)
        assert still_exists.id == link.record_id

        # Re-link existing record
        relink = await case_entities_service.add_record(
            case.id, link.record_id, entity_id
        )
        assert relink.record_id == link.record_id

    async def test_filter_by_entity(
        self,
        scenario: str,
        cases_service: CasesService,
        case_entities_service: CaseEntitiesService,
        entities_admin_service: CustomEntitiesService,
    ) -> None:
        """Test filtering records by entity ID."""
        # Create a case
        case = await cases_service.create_case(
            CaseCreate(
                summary=f"Multi-entity test: {scenario}",
                description="Test filtering by entity",
                status=CaseStatus.NEW,
                priority=CasePriority.LOW,
                severity=CaseSeverity.LOW,
            )
        )

        # Create two different entities
        entity1_id, data1, _ = await _prepare_scenario(scenario, entities_admin_service)

        # Create a simple second entity
        entity2 = await entities_admin_service.create_entity(
            name=f"test_entity_{scenario}", display_name="Test Entity"
        )
        await entities_admin_service.create_field(
            entity2.id, "name", FieldType.TEXT, "Name"
        )
        entity2_id = entity2.id
        data2 = {"name": "Test Record"}

        # Add records from both entities
        link1 = await case_entities_service.create_record(case.id, entity1_id, data1)
        link2 = await case_entities_service.create_record(case.id, entity2_id, data2)

        # List all records
        all_results = await case_entities_service.list_records(case.id)
        assert len(all_results) == 2

        # Filter by entity1
        entity1_results = await case_entities_service.list_records(case.id, entity1_id)
        assert len(entity1_results) == 1
        item1 = entity1_results[0]
        assert item1.record_id == link1.record_id
        assert item1.entity is not None
        assert item1.entity.id == entity1_id
        assert item1.record is not None
        assert item1.record.id == link1.record_id

        # Filter by entity2
        entity2_results = await case_entities_service.list_records(case.id, entity2_id)
        assert len(entity2_results) == 1
        item2 = entity2_results[0]
        assert item2.record_id == link2.record_id
        assert item2.entity is not None
        assert item2.entity.id == entity2_id
        assert item2.record is not None
        assert item2.record.id == link2.record_id


@pytest.mark.anyio
class TestCaseEntitiesServiceAdditional:
    """Additional tests for case entities service that don't need parametrization."""

    async def test_get_record_by_slug(
        self,
        cases_service: CasesService,
        case_entities_service: CaseEntitiesService,
        entities_admin_service: CustomEntitiesService,
    ) -> None:
        """Test retrieving records by slug field."""
        # Create a case
        case = await cases_service.create_case(
            CaseCreate(
                summary="Slug retrieval test",
                description="Test getting records by slug",
                status=CaseStatus.NEW,
                priority=CasePriority.LOW,
                severity=CaseSeverity.LOW,
            )
        )

        # Create entity with a 'name' field
        entity = await entities_admin_service.create_entity(
            name="test_entity", display_name="Test Entity"
        )
        await entities_admin_service.create_field(
            entity.id, "name", FieldType.TEXT, "Name"
        )
        await entities_admin_service.create_field(
            entity.id, "description", FieldType.TEXT, "Description"
        )

        # Create records with specific names
        record1_data = {"name": "unique-slug-1", "description": "First record"}
        record2_data = {"name": "unique-slug-2", "description": "Second record"}

        link1 = await case_entities_service.create_record(
            case.id, entity.id, record1_data
        )
        link2 = await case_entities_service.create_record(
            case.id, entity.id, record2_data
        )

        # Test retrieval by slug
        retrieved = await case_entities_service.get_record_by_slug(
            case.id, "test_entity", "unique-slug-1"
        )
        assert retrieved.id == link1.record_id
        assert retrieved.field_data["name"] == "unique-slug-1"
        assert retrieved.field_data["description"] == "First record"

        # Test with non-default slug field
        retrieved2 = await case_entities_service.get_record_by_slug(
            case.id, "test_entity", "Second record", slug_field="description"
        )
        assert retrieved2.id == link2.record_id
        assert retrieved2.field_data["name"] == "unique-slug-2"

        # Test error when slug doesn't exist
        with pytest.raises(TracecatNotFoundError):
            await case_entities_service.get_record_by_slug(
                case.id, "test_entity", "non-existent-slug"
            )

        # Test error when record not linked to case
        # Create another case and try to get record from first case
        other_case = await cases_service.create_case(
            CaseCreate(
                summary="Other case",
                description="Different case",
                status=CaseStatus.NEW,
                priority=CasePriority.LOW,
                severity=CaseSeverity.LOW,
            )
        )

        with pytest.raises(TracecatNotFoundError):
            await case_entities_service.get_record_by_slug(
                other_case.id, "test_entity", "unique-slug-1"
            )

    async def test_list_records_with_missing_data(
        self,
        cases_service: CasesService,
        case_entities_service: CaseEntitiesService,
        entities_admin_service: CustomEntitiesService,
    ) -> None:
        """Test that list_records handles missing entities/records gracefully."""
        # Create a case
        case = await cases_service.create_case(
            CaseCreate(
                summary="Missing data test",
                description="Test handling of missing entities/records",
                status=CaseStatus.NEW,
                priority=CasePriority.LOW,
                severity=CaseSeverity.LOW,
            )
        )

        # Create multiple entities and records to test partial failures
        entity1 = await entities_admin_service.create_entity(
            name="temp_entity_1", display_name="Temp Entity 1"
        )
        await entities_admin_service.create_field(
            entity1.id, "name", FieldType.TEXT, "Name"
        )

        entity2 = await entities_admin_service.create_entity(
            name="temp_entity_2", display_name="Temp Entity 2"
        )
        await entities_admin_service.create_field(
            entity2.id, "name", FieldType.TEXT, "Name"
        )

        # Create records
        link1 = await case_entities_service.create_record(
            case.id, entity1.id, {"name": "Test Record 1"}
        )
        _ = await case_entities_service.create_record(
            case.id, entity2.id, {"name": "Test Record 2"}
        )

        # Verify initial state
        results = await case_entities_service.list_records(case.id)
        assert len(results) == 2

        # Delete entity2 (this will cascade delete rec2 and link2)
        await entities_admin_service.delete_entity(entity2.id)

        # Should now only have one record
        results = await case_entities_service.list_records(case.id)
        assert len(results) == 1
        item = results[0]
        assert item.id == link1.id
        assert item.entity is not None
        assert item.entity.id == entity1.id
        assert item.record is not None
        assert item.record.id == link1.record_id

        # Test that CASCADE delete works correctly
        # When we delete a record, the link should also be deleted
        link3 = await case_entities_service.create_record(
            case.id, entity1.id, {"name": "Test Record 3"}
        )

        # Verify we have 2 records
        results = await case_entities_service.list_records(case.id)
        assert len(results) == 2

        # Delete the record (link should CASCADE delete)
        await entities_admin_service.delete_record(link3.record_id)

        # Should be back to 1 record
        results = await case_entities_service.list_records(case.id)
        assert len(results) == 1
        item_final = results[0]
        assert item_final.id == link1.id
        assert item_final.entity is not None
        assert item_final.record is not None

        # The CASCADE delete behavior ensures referential integrity
        # Links can't exist without their referenced entities/records
        # This is by design for data consistency

    async def test_complete_data_structure_for_frontend(
        self,
        cases_service: CasesService,
        case_entities_service: CaseEntitiesService,
        entities_admin_service: CustomEntitiesService,
    ) -> None:
        """Test that all data needed by frontend is present and correctly formatted."""
        # Create a case
        case = await cases_service.create_case(
            CaseCreate(
                summary="Frontend data validation",
                description="Comprehensive test of data structure",
                status=CaseStatus.NEW,
                priority=CasePriority.HIGH,
                severity=CaseSeverity.HIGH,
            )
        )

        # Use the all_fields scenario for comprehensive testing
        entity_id, record_data, _ = await _prepare_all_field_types(
            entities_admin_service
        )

        # Get entity details for validation
        entity = await entities_admin_service.get_entity(entity_id)
        fields = await entities_admin_service.list_fields(entity_id)
        relations = await entities_admin_service.list_relations(entity_id)

        # Create and link the record
        link = await case_entities_service.create_record(
            case.id, entity_id, record_data
        )

        # List records and validate complete structure
        results = await case_entities_service.list_records(case.id)
        assert len(results) == 1

        item = results[0]

        # Verify CaseRecordLink structure
        assert item.id == link.id
        assert item.case_id == case.id
        assert item.entity_id == entity_id
        assert item.record_id == link.record_id

        # Verify Entity has all required fields for frontend
        assert item.entity is not None
        assert item.entity.id == entity.id
        assert item.entity.name == "all_types"
        assert item.entity.display_name == "All Types"
        assert item.entity.description is None  # Can be None
        assert item.entity.icon is None  # Can be None
        assert item.entity.is_active is True

        # Verify Record has all required fields
        assert item.record is not None
        assert item.record.id == link.record_id
        assert item.record.entity_id == entity_id
        assert item.record.field_data is not None

        # Verify all field types are properly serialized in field_data
        field_data = item.record.field_data

        # Primitive types
        assert "integer_value" in field_data
        assert isinstance(field_data["integer_value"], int)
        assert field_data["integer_value"] == 123

        assert "number_value" in field_data
        assert isinstance(field_data["number_value"], float)
        assert field_data["number_value"] == 3.14

        assert "text_value" in field_data
        assert isinstance(field_data["text_value"], str)
        assert field_data["text_value"] == "hello"

        assert "boolean_value" in field_data
        assert isinstance(field_data["boolean_value"], bool)
        assert field_data["boolean_value"] is True

        # Date/time types (should be ISO format strings)
        assert "datetime_value" in field_data
        assert isinstance(field_data["datetime_value"], str)

        assert "date_value" in field_data
        assert isinstance(field_data["date_value"], str)

        # Array types
        assert "array_text" in field_data
        assert isinstance(field_data["array_text"], list)
        assert field_data["array_text"] == ["a", "b"]

        assert "array_integer" in field_data
        assert isinstance(field_data["array_integer"], list)
        assert field_data["array_integer"] == [1, 2, 3]

        assert "array_number" in field_data
        assert isinstance(field_data["array_number"], list)
        assert field_data["array_number"] == [1.5, 2.5]

        # Select types
        assert "select_value" in field_data
        assert field_data["select_value"] == "B"

        assert "multi_select" in field_data
        assert isinstance(field_data["multi_select"], list)
        assert set(field_data["multi_select"]) == {"x", "z"}

        # Relation fields ARE resolved and included in field_data for case entities
        # The case entities service resolves relations at read time
        assert "department" in field_data
        # Department relation should be resolved to the related record's field_data
        assert isinstance(field_data["department"], dict)
        assert field_data["department"]["name"] == "Engineering"

        # One_to_many relations are resolved as lists of related records
        assert "projects" in field_data
        assert isinstance(field_data["projects"], list)
        assert len(field_data["projects"]) == 2
        # Projects should be resolved to their field_data
        project_codes = {p["code"] for p in field_data["projects"]}
        assert project_codes == {"PJT-A", "PJT-B"}

        # Verify that field metadata would be available for UI rendering
        # (Frontend would need to call a separate endpoint to get field definitions)
        field_keys = {f.field_key for f in fields}
        assert "integer_value" in field_keys
        assert "select_value" in field_keys

        # Relations are now separate from fields
        relation_keys = {r.source_key for r in relations}
        assert "department" in relation_keys
        assert "projects" in relation_keys
