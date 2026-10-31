# The whole deployment (SPEC §9, D-57): one image, a playground app and a nightly gate
# job, one Azure Files share mounted at /data in both, logs and three alerts. Nothing
# secret is in these files: the model key, the hash salt, the session key and the
# sign-in accounts are Container App secrets set by `make secrets` and `make users`, and
# the share's link gets the account key from one az command to the next. The state is
# another matter: it holds the storage account's keys and, once refreshed, the secrets'
# values, so it is kept local, git-ignored and owner-only (the Makefile's umask 077).

locals {
  tags = { project = var.prefix, managed_by = "terraform" }
  # the app and the job exist once an image has been built (`make image`)
  ready = var.image_digest == "" ? 0 : 1
  image = "${azurerm_container_registry.this.login_server}/pirtis@${var.image_digest}"
  # the environment's link to the share; `make storage-link` creates it under this name
  storage_link = "data"
  # declared here so the containers can reference them; their values are set by CLI
  secrets = ["llm-api-key", "hash-salt", "session-key", "users"]
}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_resource_group" "this" {
  name     = "${var.prefix}-rg"
  location = var.location
  tags     = local.tags
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.prefix}-logs"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 1 # a runaway log can't run up the bill
  tags                = local.tags
}

resource "azurerm_container_app_environment" "this" {
  name                       = "${var.prefix}-env"
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  tags                       = local.tags
  # Azure gives a new environment this profile; declared so plans don't fight it
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

resource "azurerm_container_registry" "this" {
  name                = "${var.prefix}acr${random_string.suffix.result}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "Basic"
  admin_enabled       = false # pulls go through the managed identity
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "pull" {
  name                = "${var.prefix}-pull"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_role_assignment" "pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.pull.principal_id
}

resource "azurerm_storage_account" "data" {
  name                            = "${var.prefix}data${random_string.suffix.result}"
  location                        = azurerm_resource_group.this.location
  resource_group_name             = azurerm_resource_group.this.name
  account_kind                    = "StorageV2"
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = true # an SMB mount in Container Apps still needs the key
  tags                            = local.tags
}

resource "azurerm_storage_share" "data" {
  name               = "pirtis-data"
  storage_account_id = azurerm_storage_account.data.id
  quota              = 1 # GiB
}

resource "azurerm_container_app" "playground" {
  count                        = local.ready
  name                         = "${var.prefix}-app"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  tags                         = local.tags
  workload_profile_name        = "Consumption" # scale to zero, billed per second

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.pull.id]
  }
  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.pull.id
  }

  dynamic "secret" {
    for_each = local.secrets
    content {
      name  = secret.value
      value = "unset" # the real value is set by CLI; until then the app refuses to start
    }
  }
  lifecycle {
    ignore_changes = [secret]
  }

  ingress {
    external_enabled           = true
    target_port                = 8000
    transport                  = "auto"
    allow_insecure_connections = false
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
    dynamic "ip_security_restriction" {
      for_each = var.allowed_ips
      content {
        name             = "allow-${replace(ip_security_restriction.value, "/[./]/", "-")}"
        action           = "Allow"
        ip_address_range = ip_security_restriction.value
      }
    }
  }

  template {
    min_replicas = 0
    max_replicas = 1
    container {
      name   = "playground"
      image  = local.image
      cpu    = 0.25
      memory = "0.5Gi"
      env {
        name        = "LLM_API_KEY"
        secret_name = "llm-api-key"
      }
      env {
        name        = "PIRTIS_HASH_SALT"
        secret_name = "hash-salt"
      }
      env {
        name        = "PIRTIS_SESSION_KEY"
        secret_name = "session-key"
      }
      env {
        name        = "PIRTIS_USERS"
        secret_name = "users"
      }
      env {
        name  = "PIRTIS_RATE_PER_MIN"
        value = tostring(var.rate_per_min)
      }
      dynamic "env" {
        for_each = var.llm_base_url == "" ? [] : [var.llm_base_url]
        content {
          name  = "LLM_BASE_URL"
          value = env.value
        }
      }
      volume_mounts {
        name = "data"
        path = "/data"
      }
      liveness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/api/health"
      }
      readiness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/api/health"
      }
    }
    volume {
      name         = "data"
      storage_type = "AzureFile"
      storage_name = local.storage_link
    }
  }
}

resource "azurerm_container_app_job" "gate" {
  count                        = local.ready
  name                         = "${var.prefix}-gate"
  location                     = azurerm_resource_group.this.location
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  replica_timeout_in_seconds   = 1800
  replica_retry_limit          = 0 # a failed gate is read, never re-run until green (D-21)
  tags                         = local.tags
  workload_profile_name        = "Consumption" # scale to zero, billed per second

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.pull.id]
  }
  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.pull.id
  }

  secret {
    name  = "llm-api-key"
    value = "unset"
  }
  secret {
    name  = "hash-salt"
    value = "unset"
  }
  lifecycle {
    ignore_changes = [secret]
  }

  schedule_trigger_config {
    cron_expression          = var.gate_cron
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    container {
      name    = "gate"
      image   = local.image
      cpu     = 0.25
      memory  = "0.5Gi"
      command = ["gate-job"]
      args    = ["--suites", var.gate_suites]
      env {
        name        = "LLM_API_KEY"
        secret_name = "llm-api-key"
      }
      env {
        name        = "PIRTIS_HASH_SALT"
        secret_name = "hash-salt"
      }
      dynamic "env" {
        for_each = var.llm_base_url == "" ? [] : [var.llm_base_url]
        content {
          name  = "LLM_BASE_URL"
          value = env.value
        }
      }
      volume_mounts {
        name = "data"
        path = "/data"
      }
    }
    volume {
      name         = "data"
      storage_type = "AzureFile"
      storage_name = local.storage_link
    }
  }
}
