{{- define "loki.config" -}}
auth_enabled: false
server:
  http_listen_port: 3100
  grpc_listen_port: 9095
  log_level: info
common:
  instance_addr: 127.0.0.1
  path_prefix: /var/loki
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory
  storage:
    filesystem:
      chunks_directory: /var/loki/chunks
      rules_directory: /var/loki/rules
schema_config:
  configs:
    - from: "2026-11-01"
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
limits_config:
  retention_period: {{ .Values.retention }}
  # OTLP logs: resource attributes such as service.name / k8s.namespace.name become
  # labels (service_name, k8s_namespace_name), the rest structured metadata.
  allow_structured_metadata: true
  volume_enabled: true
  discover_log_levels: true
  ingestion_rate_mb: 4
  ingestion_burst_size_mb: 8
compactor:
  working_directory: /var/loki/compactor
  compaction_interval: 10m
  retention_enabled: true
  retention_delete_delay: 2h
  delete_request_store: filesystem
# Log patterns for Grafana's Logs Drilldown.
pattern_ingester:
  enabled: true
query_range:
  results_cache:
    cache:
      embedded_cache:
        enabled: true
        max_size_mb: 64
analytics:
  reporting_enabled: false
{{- end }}
