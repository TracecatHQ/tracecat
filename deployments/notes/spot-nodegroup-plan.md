# Spot Node Group Plan (Managed Node Group)

## Goal
Replace Karpenter-based spot capacity with a simple EKS managed node group that uses Spot instances.

## Terraform changes
- Add new variables (root + module):
  - `spot_node_group_enabled` (bool, default false)
  - `spot_node_instance_types` (list(string), default ["m7g.2xlarge"])
  - `spot_node_desired_size` (number, default 0)
  - `spot_node_min_size` (number, default 0)
  - `spot_node_max_size` (number, default 5)
- Pass new variables from `terraform/aws/main.tf` into the `eks` module.
- Add a new managed node group in `terraform/aws/modules/eks/cluster.tf`:
  - `capacity_type = "SPOT"`
  - `instance_types = var.spot_node_instance_types`
  - `ami_type = var.node_ami_type`
  - `disk_size = var.node_disk_size`
  - labels include `tracecat.com/capacity = "spot"` and `tracecat.com/purpose = "tracecat"`.
  - scaling config uses `spot_node_*` variables.
- Update Tracecat scheduling defaults in `terraform/aws/modules/eks/helm.tf` to prefer spot nodes when `spot_node_group_enabled = true`.
  - Reuse the existing affinity/topologySpread logic previously tied to Karpenter.

## Revert Karpenter changes
- Remove the explicit `karpenter-crd` Helm release and restore the controller release to its prior state.
- Restore Karpenter manifest files to use fixed API versions and `spec.role` (remove instanceProfile toggle).
- Revert `karpenter_chart_version` default to `null` and remove the extra Karpenter API/version/name variables added previously.
- Remove the Karpenter CRD section added to `terraform/aws/README.md`.

## Notes
- This keeps the on-demand node group intact and adds a spot group that can be turned on when needed.
- Spot capacity is best-effort; workloads still run on on-demand if spot is unavailable.
