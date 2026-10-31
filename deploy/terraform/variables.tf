variable "prefix" {
  description = "Starts every resource name."
  type        = string
  default     = "pirtis"
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,9}$", var.prefix))
    error_message = "3-10 lowercase letters and digits, starting with a letter."
  }
}

variable "location" {
  description = "An EU region (Sweden Central if Germany West Central has no capacity)."
  type        = string
  default     = "germanywestcentral"
  validation {
    condition = contains([
      "germanywestcentral", "swedencentral", "westeurope", "northeurope", "francecentral",
      "italynorth", "polandcentral", "spaincentral", "norwayeast",
    ], var.location)
    error_message = "An EU or EEA region only."
  }
}

variable "image_digest" {
  description = "The image to run, by digest (sha256:...), as `make image` wrote it. Empty only for `make infra`."
  type        = string
  default     = ""
  validation {
    condition     = var.image_digest == "" || can(regex("^sha256:[0-9a-f]{64}$", var.image_digest))
    error_message = "A digest, sha256:<64 hex>; never a tag, which can move after the gate passed."
  }
}

variable "alert_email" {
  description = "Who the alerts email."
  type        = string
}

variable "allowed_ips" {
  description = "Optional ingress allow-list (CIDRs). Empty: reachable from anywhere, behind the sign-in."
  type        = list(string)
  default     = []
}

variable "llm_base_url" {
  description = "Azure OpenAI's v1 endpoint, or empty for OpenAI."
  type        = string
  default     = ""
}

variable "gate_cron" {
  description = "When the gate job runs, UTC."
  type        = string
  default     = "0 3 * * *"
}

variable "gate_suites" {
  description = "nightly (the given cases and the red team) or all."
  type        = string
  default     = "nightly"
  validation {
    condition     = contains(["nightly", "all"], var.gate_suites)
    error_message = "nightly or all."
  }
}

variable "rate_per_min" {
  description = "The playground's cap on requests that spend model tokens, per minute."
  type        = number
  default     = 30
}

variable "latency_p95_ms" {
  description = "The slow-turns alert fires above this p95 over an hour."
  type        = number
  default     = 3000
}
