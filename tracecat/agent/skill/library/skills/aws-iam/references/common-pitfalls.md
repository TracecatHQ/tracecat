# Common Pitfalls

## Assuming Direct Name Mapping

API operation names and IAM action names frequently differ. Always query the service authorization reference.

```json
{
  "Action": "dynamodb:QueryItems"
}
```

Wrong — the correct action is `dynamodb:Query`.

## Missing Required Actions for an Operation

Some operations require multiple IAM actions. For example, `dynamodb:BatchExecuteStatement` requires `dynamodb:PartiQLDelete`, `dynamodb:PartiQLInsert`, `dynamodb:PartiQLSelect`, and `dynamodb:PartiQLUpdate`.

## Using Wildcard Resources Unnecessarily

```json
{
  "Action": "s3:GetObject",
  "Resource": "*"
}
```

Too broad. Specify bucket and object paths: `arn:aws:s3:::my-bucket/*`.

## ForAnyValue/ForAllValues on Non-Array Condition Keys

`ForAnyValue` and `ForAllValues` MUST only be used with array-typed condition keys.

**Check the type** using the service reference `ConditionKeys` array:

- **Array types** (safe for set operators): `ArrayOfString`, `ArrayOfARN`, `ArrayOfNumeric`
  - Examples: `aws:TagKeys`, `dynamodb:Attributes`, `dynamodb:LeadingKeys`
- **Scalar types** (do NOT use set operators): `String`, `Bool`, `ARN`, `Numeric`
  - Examples: `dynamodb:EnclosingOperation`, `dynamodb:FullTableScan`

## ForAnyValue in Deny Statements Without Null Check

`ForAnyValue` evaluates to `FALSE` when the context key does not exist. Deny statements using `ForAnyValue` will not block requests when the key is missing.

❌ **Incorrect:**

```json
{
  "Effect": "Deny",
  "Principal": "*",
  "Action": ["s3:GetObject", "s3:PutObject"],
  "Resource": "arn:aws:s3:::my-bucket/*",
  "Condition": {
    "ForAnyValue:StringNotLike": {
      "aws:VpceOrgPaths": "o-abcdefg/r-12345/ou-123456/*"
    }
  }
}
```

✅ **Correct — add a separate Null-check statement:**

```json
{
  "Effect": "Deny",
  "Principal": "*",
  "Action": ["s3:GetObject", "s3:PutObject"],
  "Resource": "arn:aws:s3:::my-bucket/*",
  "Condition": {
    "ForAnyValue:StringNotLike": {
      "aws:VpceOrgPaths": "o-abcdefg/r-12345/ou-123456/*"
    }
  }
},
{
  "Effect": "Deny",
  "Principal": "*",
  "Action": ["s3:GetObject", "s3:PutObject"],
  "Resource": "arn:aws:s3:::my-bucket/*",
  "Condition": {
    "Null": { "aws:VpceOrgPaths": "true" }
  }
}
```

## ForAllValues in Allow Statements Without Null Check

`ForAllValues` evaluates to `TRUE` when the context key does not exist. Allow statements using `ForAllValues` will grant access when the key is missing.

❌ **Incorrect:**

```json
{
  "Effect": "Allow",
  "Action": "s3:PutObject",
  "Resource": "*",
  "Condition": {
    "ForAllValues:StringEquals": { "aws:TagKeys": "a" }
  }
}
```

✅ **Correct — require the key to exist:**

```json
{
  "Effect": "Allow",
  "Action": "s3:PutObject",
  "Resource": "*",
  "Condition": {
    "Null": { "aws:TagKeys": "false" },
    "ForAllValues:StringEquals": { "aws:TagKeys": "a" }
  }
}
```

`ForAllValues` in Allow statements is risky. If you must use it, always combine with `Null: false`.

## Adding Conditions When They Are Not Needed

For identity policies, most policies only need Actions and Resources. Add conditions only when:

- Restricting sensitive actions (e.g., requiring MFA for `iam:DeleteUser`)
- Implementing tag-based access control (TBAC)
- Enforcing organizational requirements (encryption, VPC restrictions)

Resource policies more commonly use conditions (VPC endpoints, source IPs, secure transport).
