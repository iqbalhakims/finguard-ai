{{/* Common labels for every object. */}}
{{- define "finguard.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/part-of: finguard
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Selector labels for a component: include "finguard.selector" (dict "name" "finguard-api") */}}
{{- define "finguard.selector" -}}
app.kubernetes.io/name: {{ .name }}
{{- end }}

{{/* Full image reference for the API/simulator image. */}}
{{- define "finguard.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end }}

{{/* Pod-level security context, merged with a component-specific runAs user. */}}
{{- define "finguard.podSecurity" -}}
{{- $base := deepCopy .root.Values.podSecurityContext -}}
{{- toYaml (mergeOverwrite $base .extra) }}
{{- end }}

{{/* Grafana root URL for the bundled Grafana (empty = Grafana default). */}}
{{- define "finguard.grafanaRootUrl" -}}
{{- .Values.grafana.rootUrl -}}
{{- end }}
