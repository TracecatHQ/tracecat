# Case file attachments feature

## File types

- Images
- Videos
- Audio
- Documents

## Use cases

### UI

- Attach files from the UI to a case.
- View files attached to a case.
- Ideally, show a thumbnail of the file in the UI.
- Download files attached to a case.
- Delete files attached to a case.

### Service layer

- Attach files to a case.
- Get files attached to a case.
- Delete files attached to a case.
- Create a case with a file attached (from a Tracecat action)

#### Implement

## General implementation

### Infra

- Use aioboto3 backed by MinIO for self-hosted deployments
- Support switching to S3 for cloud deployments via environment variables
- Follow the pattern established in PR #649 for object store configuration

### DB Schema

- File table as first-class entity with CAS using SHA256 hashing
- CaseAttachment link table for many-to-many relationship between cases and files
- Support for file metadata storage (MIME type, size, creation date, original filename)

## Implementation Details

### Storage Backend Configuration

- **Primary**: aioboto3 with MinIO for self-hosted
- **Secondary**: AWS S3 for cloud deployments
- **Configuration**: Environment variables similar to the object store pattern in PR #649:
  - `MINIO_ENDPOINT_URL`
  - `MINIO_ACCESS_KEY`
  - `MINIO_SECRET_KEY`
  - `MINIO_BUCKET`
- **Path Structure**: `/blob/{sha256}` for content-addressable storage

### File Restrictions

- **Size Limit**: 10MB maximum (reasonable for case attachments while preventing abuse)
- **File Types**: Restricted to safe file types for security:
  - Documents: PDF, DOC, DOCX, TXT, RTF
  - Images: PNG, JPG, JPEG, GIF, WEBP
  - Archives: ZIP (with virus scanning consideration)
  - Logs: LOG, CSV, JSON, XML
  - **Blocked**: Executable files (.exe, .bat, .sh, .ps1, etc.)

### Thumbnail Generation

- **Scope**: Descoped for initial implementation
- **Architecture**: Design flexible schema to add thumbnail_url field later
- **Future Support**: Images, PDFs, document previews

### File Metadata and Security

- **Metadata Storage**:
  - MIME type detection
  - File size
  - Creation timestamp
  - Original filename
  - SHA256 hash for deduplication
- **Hash Integration**: NOT integrated with existing IoC extraction (keep concerns separated)
- **Virus Scanning**: **Recommended** - integrate ClamAV or similar for uploaded files
  - Can be implemented as async background job
  - Mark files as "pending scan" until cleared
  - Critical for SOAR platform security

### Access Control

- **Permissions**: Follow workspace-level permissions (same as cases)
- **Deletion**:
  - Immediate deletion from case (remove CaseAttachment link)
  - **Soft deletion** of File entity (mark for cleanup)
  - Background job for actual blob cleanup after grace period
  - **Admin-only deletion** of File entities
- **Download**: Workspace members with case access

### Database Migration

- **Migration**: New Alembic migration auto-generated from schema
- **Backward Compatibility**: No breaking changes to existing case data
- **Schema Design**:

```sql
-- File table (first-class entity)
CREATE TABLE files (
    id UUID PRIMARY KEY,
    sha256 VARCHAR(64) UNIQUE NOT NULL,  -- Content-addressable key
    original_filename VARCHAR(255) NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    size_bytes BIGINT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    deleted_at TIMESTAMP WITH TIME ZONE NULL,  -- Soft deletion
    virus_scan_status VARCHAR(20) DEFAULT 'pending',  -- pending, clean, infected
    owner_id UUID NOT NULL  -- Workspace ID
);

-- Link table for case attachments
CREATE TABLE case_attachments (
    id UUID PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    file_id UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    attached_by UUID REFERENCES users(id),
    attached_at TIMESTAMP WITH TIME ZONE NOT NULL,
    UNIQUE(case_id, file_id)
);
```

### API Design

- **RESTful Endpoints**:
  - `GET /api/v1/cases/{case_id}/attachments` - List case attachments
  - `POST /api/v1/cases/{case_id}/attachments` - Upload file to case
  - `DELETE /api/v1/cases/{case_id}/attachments/{attachment_id}` - Remove attachment
  - `GET /api/v1/files/{file_id}/download` - Download file (with access check)
  - `DELETE /api/v1/files/{file_id}` - Admin delete file entity
- **Multipart Upload**: Support for large files using multipart/form-data
- **Streaming**: Support streaming downloads for large files

### Frontend Integration

- **Component**: Enhance existing placeholder in `case-panel-view.tsx`
- **Upload UX**:
  - **Drag & Drop**: React Dropzone (https://react-dropzone.js.org/)
  - **Click Upload**: Traditional file picker fallback
  - **Progress**: Upload progress indicators
- **File Display**:
  - File list with metadata (name, size, upload date, uploaded by)
  - Download links
  - Delete buttons (with confirmation)
- **Validation**: Client-side file type and size validation before upload

## Main concerns

### ✅ Resolved: File as First-Class Concept

**Decision**: YES - Files should be first-class entities in the SOAR platform
**Rationale**: Critical for evidence management, cross-case correlation, and forensic analysis

### ✅ Resolved: Workflow File Storage Scope

**Decision**: Scope limited to case attachments only
**Rationale**: Prevents workflow artifact bloat while providing essential case management functionality

### ✅ Resolved: Content-Addressable Storage (CAS)

**Decision**: YES - Use SHA256-based CAS with `/blob/{sha256}` structure
**Rationale**: Automatic deduplication, integrity verification, and efficient storage

## Implementation Priority

1. **Phase 1**: Core infrastructure (File/CaseAttachment tables, S3/MinIO backend)
2. **Phase 2**: Basic upload/download API and frontend
3. **Phase 3**: Enhanced UX (drag-drop, progress, validation)
4. **Phase 4**: Security features (virus scanning, admin controls)
5. **Phase 5**: Advanced features (thumbnails, metadata search)

## Dependencies

- aioboto3 (already available)
- react-dropzone for frontend
- ClamAV or similar for virus scanning (optional Phase 4)
- Alembic migration system (existing)
