resource "aws_s3_bucket" "tracecat" {
  bucket = "tracecat-bucket"
}

resource "aws_s3_bucket_ownership_controls" "tracecat" {
  bucket = aws_s3_bucket.tracecat.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_acl" "tracecat" {
  bucket = aws_s3_bucket.tracecat.id
  acl    = "private"

  depends_on = [
    aws_s3_bucket_ownership_controls.tracecat
  ]
}

resource "aws_s3_bucket_versioning" "tracecat" {
  bucket = aws_s3_bucket.tracecat.id
  versioning_configuration {
    status = "Enabled"
  }
}
