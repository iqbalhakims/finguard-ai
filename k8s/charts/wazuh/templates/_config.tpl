{{/* Config files rendered into the ConfigMaps (indexer.yaml, dashboard.yaml, agent.yaml). */}}

{{- define "wazuh.indexerConfig" -}}
cluster.name: wazuh
node.name: ${NODE_NAME}
network.host: 0.0.0.0
discovery.type: single-node
node.store.allow_mmap: false
path.data: /var/lib/wazuh-indexer
path.logs: /var/log/wazuh-indexer
plugins.security.ssl.http.pemcert_filepath: /usr/share/wazuh-indexer/config/certs/node.pem
plugins.security.ssl.http.pemkey_filepath: /usr/share/wazuh-indexer/config/certs/node-key.pem
plugins.security.ssl.http.pemtrustedcas_filepath: /usr/share/wazuh-indexer/config/certs/root-ca.pem
plugins.security.ssl.transport.pemcert_filepath: /usr/share/wazuh-indexer/config/certs/node.pem
plugins.security.ssl.transport.pemkey_filepath: /usr/share/wazuh-indexer/config/certs/node-key.pem
plugins.security.ssl.transport.pemtrustedcas_filepath: /usr/share/wazuh-indexer/config/certs/root-ca.pem
plugins.security.ssl.http.enabled: true
plugins.security.ssl.transport.enforce_hostname_verification: false
plugins.security.ssl.transport.resolve_hostname: false
plugins.security.authcz.admin_dn:
  - "CN=admin,O=FinGuard,C=SG"
plugins.security.nodes_dn:
  - "CN=indexer,O=FinGuard,C=SG"
plugins.security.check_snapshot_restore_write_privileges: true
plugins.security.enable_snapshot_restore_privilege: true
plugins.security.restapi.roles_enabled:
  - "all_access"
  - "security_rest_api_access"
plugins.security.allow_default_init_securityindex: true
cluster.routing.allocation.disk.threshold_enabled: false
compatibility.override_main_response_version: true
{{- end }}

{{- define "wazuh.dashboardConfig" -}}
server.host: 0.0.0.0
server.port: 5601
server.publicBaseUrl: {{ .Values.publicUrl }}
server.ssl.enabled: false
opensearch.hosts: https://indexer:9200
opensearch.ssl.verificationMode: full
opensearch.ssl.certificateAuthorities: ["/usr/share/wazuh-dashboard/certs/root-ca.pem"]
opensearch.requestHeadersWhitelist: [ authorization,securitytenant ]
opensearch_security.multitenancy.enabled: false
opensearch_security.readonly_mode.roles: ["kibana_read_only"]
opensearch_security.cookie.secure: true
opensearch_security.cookie.ttl: 900000
opensearch_security.session.ttl: 900000
opensearch_security.session.keepalive: true
uiSettings.overrides.defaultRoute: /app/wz-home
{{- end }}

{{- define "wazuh.agentConfig" -}}
<ossec_config>
  <client>
    <server>
      <address>CHANGE_MANAGER_IP</address>
      <port>CHANGE_MANAGER_PORT</port>
      <protocol>tcp</protocol>
    </server>
    <notify_time>10</notify_time>
    <time-reconnect>60</time-reconnect>
    <auto_restart>yes</auto_restart>
    <crypto_method>aes</crypto_method>
    <enrollment>
      <enabled>yes</enabled>
      <manager_address>CHANGE_ENROLL_IP</manager_address>
      <port>CHANGE_ENROLL_PORT</port>
      <agent_name>CHANGE_AGENT_NAME</agent_name>
      <authorization_pass_path>etc/authd.pass</authorization_pass_path>
      <groups>CHANGE_AGENT_GROUP</groups>
    </enrollment>
  </client>

  <client_buffer>
    <disabled>no</disabled>
    <queue_size>5000</queue_size>
    <events_per_second>500</events_per_second>
  </client_buffer>

  <!-- File integrity monitoring of the node: config in real time, binaries hourly. -->
  <syscheck>
    <disabled>no</disabled>
    <frequency>3600</frequency>
    <scan_on_start>yes</scan_on_start>
    <directories realtime="yes" report_changes="no">/host/etc</directories>
    <directories>/host/usr/bin,/host/usr/sbin,/host/usr/local/bin</directories>
    <ignore>/host/etc/mtab</ignore>
    <ignore>/host/etc/resolv.conf</ignore>
    <ignore>/host/etc/random-seed</ignore>
    <ignore>/host/etc/adjtime</ignore>
    <ignore type="sregex">.log$|.swp$</ignore>
    <skip_nfs>yes</skip_nfs>
    <skip_dev>yes</skip_dev>
    <skip_proc>yes</skip_proc>
    <skip_sys>yes</skip_sys>
    <process_priority>10</process_priority>
    <max_eps>50</max_eps>
  </syscheck>

  <rootcheck>
    <disabled>no</disabled>
    <check_files>yes</check_files>
    <check_trojans>yes</check_trojans>
    <check_dev>yes</check_dev>
    <check_sys>yes</check_sys>
    <check_pids>yes</check_pids>
    <check_ports>yes</check_ports>
    <check_if>yes</check_if>
    <frequency>43200</frequency>
    <rootkit_files>etc/shared/rootkit_files.txt</rootkit_files>
    <rootkit_trojans>etc/shared/rootkit_trojans.txt</rootkit_trojans>
    <skip_nfs>yes</skip_nfs>
  </rootcheck>

  <!-- Inventory: processes, ports, network, hardware of the node. Packages and OS are
       off: they would describe this container's OS, not the node's. -->
  <wodle name="syscollector">
    <disabled>no</disabled>
    <interval>1h</interval>
    <scan_on_start>yes</scan_on_start>
    <hardware>yes</hardware>
    <os>no</os>
    <network>yes</network>
    <packages>no</packages>
    <ports all="no">yes</ports>
    <processes>yes</processes>
  </wodle>

  <!-- CIS-style policy checks would assess this container's OS: off. -->
  <sca>
    <enabled>no</enabled>
  </sca>

  <!-- Never act on the node from inside a pod. -->
  <active-response>
    <disabled>yes</disabled>
  </active-response>

  {{- range .Values.agent.logFiles }}
  <localfile>
    <log_format>syslog</log_format>
    <location>{{ . }}</location>
  </localfile>
  {{- end }}

  <logging>
    <log_format>plain</log_format>
  </logging>
</ossec_config>
{{- end }}
