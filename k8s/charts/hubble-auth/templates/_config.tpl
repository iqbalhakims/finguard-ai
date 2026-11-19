{{- define "hubble-auth.nginxConf" -}}
# Started with `nginx -c` (not the image entrypoint), on a read-only root filesystem:
# everything nginx writes goes to /tmp (emptyDir).
worker_processes 1;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;

events {
  worker_connections 1024;
}

http {
  server_tokens off;
  access_log /dev/stdout;
  client_body_temp_path /tmp/client_temp;
  proxy_temp_path /tmp/proxy_temp;
  fastcgi_temp_path /tmp/fastcgi_temp;
  uwsgi_temp_path /tmp/uwsgi_temp;
  scgi_temp_path /tmp/scgi_temp;

  server {
    listen 8080;

    # Probes only; says nothing about the cluster.
    location = /healthz {
      access_log off;
      return 200 "ok\n";
    }

    location / {
      auth_basic "Hubble";
      auth_basic_user_file /etc/hubble-auth/htpasswd;   # re-read per request: no restart on rotation

      proxy_pass {{ .Values.upstream }};
      proxy_http_version 1.1;
      proxy_set_header Authorization "";   # the password stops here
      # The UI streams flows over gRPC-web: pass bytes through as they arrive and keep
      # long-lived streams open.
      proxy_buffering off;
      proxy_read_timeout 1h;
      proxy_send_timeout 1h;
    }
  }
}
{{- end }}
