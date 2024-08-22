output "endpoint_details" {
  value = <<EOS
--------------------------
Tracecat Information
--------------------

-------------
Endpoints
-------------
App: https://${var.cname_record_app}.${var.domain_name}
API: https://${var.cname_record_api}.${var.domain_name}

EOS
}
