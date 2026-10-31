# Three alerts, each with an owner and an action (SPEC §10, D-29), on the JSON lines the
# containers print: the gate job's one {"event": "gate"} line per run and the
# playground's {"event": "turn"} line per call turn. Gate and replay turns are never
# logged (D-80, D-82), so fault cases can't fire them. Each query unions an empty table
# first, so the rules can be created before the first log line exists.

locals {
  console = <<-KQL
    union isfuzzy=true (datatable(TimeGenerated: datetime, Log_s: string)[]), (ContainerAppConsoleLogs_CL)
  KQL
}

resource "azurerm_monitor_action_group" "owner" {
  name                = "${var.prefix}-owner"
  resource_group_name = azurerm_resource_group.this.name
  short_name          = var.prefix
  tags                = local.tags
  email_receiver {
    name                    = "owner"
    email_address           = var.alert_email
    use_common_alert_schema = true
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "gate" {
  name                    = "${var.prefix}-gate-regression"
  display_name            = "Gate regression"
  description             = "Owner: the repo owner. A gate run failed or the job broke. Open the admin console's Reliability view and the job's line: llm_errors above zero or an error points at the model service or the job, otherwise follow SPEC §14 to the change that broke the agent. Never re-run until green."
  resource_group_name     = azurerm_resource_group.this.name
  location                = azurerm_resource_group.this.location
  scopes                  = [azurerm_log_analytics_workspace.this.id]
  severity                = 1
  evaluation_frequency    = "PT15M"
  window_duration         = "PT1H"
  auto_mitigation_enabled = true
  tags                    = local.tags
  criteria {
    query                   = <<-KQL
      ${trimspace(local.console)}
      | where Log_s startswith '{"event": "gate"'
      | extend g = parse_json(Log_s)
      | where tobool(g.passed) == false
      | project TimeGenerated, agent_version = tostring(g.agent_version), failed = tostring(g.failed), llm_errors = toint(g.llm_errors), error = tostring(g.error)
    KQL
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0
    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }
  action {
    action_groups = [azurerm_monitor_action_group.owner.id]
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "errors" {
  name                    = "${var.prefix}-agent-errors"
  display_name            = "Agent errors"
  description             = "Owner: the repo owner. A call turn fell back after an error, or an API call failed, in the last hour. Read that turn's record in the admin console's Traces and replay the call in the simulator."
  resource_group_name     = azurerm_resource_group.this.name
  location                = azurerm_resource_group.this.location
  scopes                  = [azurerm_log_analytics_workspace.this.id]
  severity                = 2
  evaluation_frequency    = "PT15M"
  window_duration         = "PT1H"
  auto_mitigation_enabled = true
  tags                    = local.tags
  criteria {
    query                   = <<-KQL
      ${trimspace(local.console)}
      | where Log_s startswith '{"event": "turn"'
      | extend t = parse_json(Log_s)
      | where tobool(t.fallback) == true or Log_s matches regex @'"status": [45][0-9][0-9]'
      | project TimeGenerated, agent_version = tostring(t.agent_version), decision = tostring(t.decision), turn_id = tostring(t.turn_id)
    KQL
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0
    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }
  action {
    action_groups = [azurerm_monitor_action_group.owner.id]
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "latency" {
  name                    = "${var.prefix}-slow-turns"
  display_name            = "Slow turns"
  description             = "Owner: the repo owner. Over the last hour, model turns' p95 latency passed ${var.latency_p95_ms} ms (at least five turns). Check the model service's latency, then the prompt's size in the turn records' tokens."
  resource_group_name     = azurerm_resource_group.this.name
  location                = azurerm_resource_group.this.location
  scopes                  = [azurerm_log_analytics_workspace.this.id]
  severity                = 3
  evaluation_frequency    = "PT15M"
  window_duration         = "PT1H"
  auto_mitigation_enabled = true
  tags                    = local.tags
  criteria {
    query                   = <<-KQL
      ${trimspace(local.console)}
      | where Log_s startswith '{"event": "turn"'
      | extend t = parse_json(Log_s)
      | where toint(t.llm_calls) > 0
      | summarize p95 = percentile(toint(t.latency_ms), 95), turns = count()
      | where turns >= 5 and p95 > ${var.latency_p95_ms}
    KQL
    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0
    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }
  action {
    action_groups = [azurerm_monitor_action_group.owner.id]
  }
}
