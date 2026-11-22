{{/* Labels for a component: include "wazuh.labels" (dict "root" $ "name" "wazuh-indexer") */}}
{{- define "wazuh.labels" -}}
app.kubernetes.io/name: {{ .name }}
app.kubernetes.io/part-of: wazuh
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version }}
{{- end }}

{{/* Env var from the credentials Secret: include "wazuh.secretEnv" (dict "root" $ "name" "X" "key" "k") */}}
{{- define "wazuh.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .root.Values.secrets.credentials }}
      key: {{ .key }}
{{- end }}
