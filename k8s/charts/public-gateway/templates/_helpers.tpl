{{/* Response headers added to every public HTTPS route. */}}
{{- define "public-gateway.securityHeaders" -}}
- type: ResponseHeaderModifier
  responseHeaderModifier:
    set:
      - name: Strict-Transport-Security
        value: max-age=31536000
      - name: X-Content-Type-Options
        value: nosniff
{{- end }}
