{{- define "opspilot.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/* Common labels. "instance" lives in the selector labels below, so pod
     templates that include both don't end up with a duplicate key. */}}
{{- define "opspilot.labels" -}}
app.kubernetes.io/name: opspilot
app.kubernetes.io/part-of: opspilot
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "opspilot.selector" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "opspilot.qdrantUrl" -}}
{{- if .Values.config.QDRANT_URL -}}
{{- .Values.config.QDRANT_URL -}}
{{- else -}}
{{- printf "http://%s-qdrant:6333" (include "opspilot.fullname" .) -}}
{{- end -}}
{{- end -}}
