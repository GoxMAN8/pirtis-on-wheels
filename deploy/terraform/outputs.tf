output "resource_group" {
  value = azurerm_resource_group.this.name
}

output "environment" {
  value = azurerm_container_app_environment.this.name
}

output "registry" {
  value = azurerm_container_registry.this.name
}

output "storage_account" {
  value = azurerm_storage_account.data.name
}

output "share" {
  value = azurerm_storage_share.data.name
}

output "storage_link" {
  value = local.storage_link
}

output "workspace_id" {
  description = "For `az monitor log-analytics query --workspace`."
  value       = azurerm_log_analytics_workspace.this.workspace_id
}

output "app" {
  value = "${var.prefix}-app"
}

output "job" {
  value = "${var.prefix}-gate"
}

output "url" {
  value = local.ready == 1 ? "https://${azurerm_container_app.playground[0].ingress[0].fqdn}" : ""
}
