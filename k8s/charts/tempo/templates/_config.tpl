{{- define "tempo.config" -}}
stream_over_http_enabled: true
server:
  http_listen_port: 3200
  grpc_listen_port: 9095
  log_level: info
distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
        http:
          endpoint: 0.0.0.0:4318
ingester:
  max_block_duration: 5m
compactor:
  compaction:
    block_retention: {{ .Values.retention }}
storage:
  trace:
    backend: local
    wal:
      path: /var/tempo/wal
    local:
      path: /var/tempo/blocks
usage_report:
  reporting_enabled: false
{{- with .Values.metricsGenerator }}
{{- if .enabled }}
# Turns spans into Prometheus metrics: service graph edges (client -> server calls)
# and per-span RED metrics, remote-written to Prometheus.
metrics_generator:
  registry:
    external_labels:
      source: tempo
  storage:
    path: /var/tempo/generator/wal
    remote_write:
      - url: {{ .remoteWriteUrl }}
        send_exemplars: true
  traces_storage:
    path: /var/tempo/generator/traces
overrides:
  defaults:
    metrics_generator:
      processors: [service-graphs, span-metrics]
{{- end }}
{{- end }}
{{- end }}
