# Bootstrap KEDA CRDs before the Tracecat chart renders ScaledObjects/TriggerAuthentication.
# This avoids first-time enablement failures when keda.enabled flips from false to true.

locals {
  tracecat_chart_lock = yamldecode(file("${path.module}/../../../helm/tracecat/Chart.lock"))
  keda_chart_version = one([
    for dependency in local.tracecat_chart_lock.dependencies :
    dependency.version
    if dependency.name == "keda"
  ])
}

data "http" "keda_crds" {
  url = format(
    "https://github.com/kedacore/keda/releases/download/v%s/keda-%s-crds.yaml",
    local.keda_chart_version,
    local.keda_chart_version
  )
}

locals {
  keda_crd_helm_labels = {
    "app.kubernetes.io/managed-by" = "Helm"
  }
  keda_crd_helm_annotations = {
    "meta.helm.sh/release-name"      = "tracecat"
    "meta.helm.sh/release-namespace" = "tracecat"
  }
  keda_crd_manifests = {
    for crd in [
      for document in split("\n---\n", trimspace(data.http.keda_crds.response_body)) :
      yamldecode(document)
      if trimspace(document) != ""
    ] :
    crd.metadata.name => merge(
      crd,
      {
        metadata = merge(
          lookup(crd, "metadata", {}),
          {
            labels = merge(
              lookup(lookup(crd, "metadata", {}), "labels", {}),
              local.keda_crd_helm_labels
            )
            annotations = merge(
              lookup(lookup(crd, "metadata", {}), "annotations", {}),
              local.keda_crd_helm_annotations
            )
          }
        )
      }
    )
    if try(crd.kind, "") == "CustomResourceDefinition"
  }
}

resource "kubernetes_manifest" "keda_crds" {
  for_each = local.keda_crd_manifests

  manifest = each.value

  # Allow Terraform to adopt/update CRDs that may have been bootstrapped manually.
  field_manager {
    force_conflicts = true
  }

  depends_on = [
    aws_eks_node_group.tracecat,
    aws_eks_node_group.tracecat_spot,
  ]
}
