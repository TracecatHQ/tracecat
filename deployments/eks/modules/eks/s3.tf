# S3 Bucket for Attachments
resource "aws_s3_bucket" "attachments" {
  bucket = local.s3_attachments_bucket

  tags = merge(var.tags, {
    Name = local.s3_attachments_bucket
  })
}

resource "aws_s3_bucket_versioning" "attachments" {
  bucket = aws_s3_bucket.attachments.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "attachments" {
  bucket = aws_s3_bucket.attachments.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "attachments" {
  bucket = aws_s3_bucket.attachments.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "attachments_bucket" {
  statement {
    sid    = "AllowTracecatS3Role"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.tracecat_s3.arn]
    }
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket"
    ]
    resources = [
      aws_s3_bucket.attachments.arn,
      "${aws_s3_bucket.attachments.arn}/*"
    ]
  }

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [
      aws_s3_bucket.attachments.arn,
      "${aws_s3_bucket.attachments.arn}/*"
    ]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "attachments" {
  bucket = aws_s3_bucket.attachments.id
  policy = data.aws_iam_policy_document.attachments_bucket.json
}

# S3 Bucket for Registry
resource "aws_s3_bucket" "registry" {
  bucket = local.s3_registry_bucket

  tags = merge(var.tags, {
    Name = local.s3_registry_bucket
  })
}

resource "aws_s3_bucket_versioning" "registry" {
  bucket = aws_s3_bucket.registry.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "registry" {
  bucket = aws_s3_bucket.registry.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "registry" {
  bucket = aws_s3_bucket.registry.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "registry_bucket" {
  statement {
    sid    = "AllowTracecatS3Role"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.tracecat_s3.arn]
    }
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket"
    ]
    resources = [
      aws_s3_bucket.registry.arn,
      "${aws_s3_bucket.registry.arn}/*"
    ]
  }

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [
      aws_s3_bucket.registry.arn,
      "${aws_s3_bucket.registry.arn}/*"
    ]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "registry" {
  bucket = aws_s3_bucket.registry.id
  policy = data.aws_iam_policy_document.registry_bucket.json
}

# S3 Bucket for Workflow Data (externalized action results, triggers, etc.)
resource "aws_s3_bucket" "workflow" {
  bucket = local.s3_workflow_bucket

  tags = merge(var.tags, {
    Name = local.s3_workflow_bucket
  })
}

resource "aws_s3_bucket_versioning" "workflow" {
  bucket = aws_s3_bucket.workflow.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "workflow" {
  bucket = aws_s3_bucket.workflow.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "workflow" {
  bucket = aws_s3_bucket.workflow.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "workflow_bucket" {
  statement {
    sid    = "AllowTracecatS3Role"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.tracecat_s3.arn]
    }
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket"
    ]
    resources = [
      aws_s3_bucket.workflow.arn,
      "${aws_s3_bucket.workflow.arn}/*"
    ]
  }

  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [
      aws_s3_bucket.workflow.arn,
      "${aws_s3_bucket.workflow.arn}/*"
    ]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "workflow" {
  bucket = aws_s3_bucket.workflow.id
  policy = data.aws_iam_policy_document.workflow_bucket.json
}
