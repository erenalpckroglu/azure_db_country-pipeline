# Azure Data Factory

[`ARMTemplateForFactory.json`](ARMTemplateForFactory.json) is the ARM template export of the factory (Data Factory Studio -> Manage -> ARM template -> Export ARM template).
Connection strings, keys, hostnames and the factory name are replaced with placeholders such as `<redacted>`,
so the file documents the pipeline but cannot be deployed as is.

## Pipeline overview

| Component | Name | Purpose |
|---|---|---|
| Pipeline | `pl_ingest_raw` | Copy the source CSV to Blob Storage, then call the Azure Function |
| Dataset | `ds_http_humdata` | Source CSV over HTTP |
| Dataset | `ds_blob_rawhumdata_global` | Raw sink in the `raw` container, dated file name |
| Dataset | `ds_test` | Small test file used during development, not used by the pipeline |
| Linked service | `httpsvteam03_humdata` | HTTP connection to the Humanitarian Data Exchange |
| Linked service | `absteam03` | Azure Blob Storage |
| Linked service | `ls_function_clean` | Calls `clean_pipeline` (function key authentication) |
| Trigger | `trigger_every15th_monthly` | 15th of every month, 01:00 W. Europe time (UTC+1 / UTC+2) |

Dynamic sink file name:

```
@concat('global_', formatDateTime(utcnow(), 'yyyyMMdd'), '.csv')
```
