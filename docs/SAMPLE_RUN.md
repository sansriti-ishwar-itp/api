# Sample run — DR pipeline against the mock adapter

Verbatim PowerShell transcript of the end-to-end demo from the
[README](../README.md#try-it-end-to-end-mock-mode).

## 1. Register a VM for DR monitoring

```powershell
$body = @{ name = "build-agent-7"; external_id = "ext-build-7"; rto_minutes = 15 } | ConvertTo-Json
$vm   = Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/vms `
          -ContentType 'application/json' -Body $body
$vmId = $vm.id
```

## 2. Trigger the DR pipeline

```powershell
$job   = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/v1/vms/$vmId/dr/trigger" `
            -ContentType 'application/json' -Body '{}'
$jobId = $job.id
```

## 3. Poll the DR job

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/v1/dr/jobs/$jobId" | ConvertTo-Json -Depth 10
```

```json
{
    "id":  "1e9b6e12668649ef89df7a102ea36458",
    "vm_id":  "87b49905708e44e285080d09fb51e857",
    "status":  "failed",
    "current_state":  "snapshotting",
    "rto_minutes":  15,
    "started_at":  "2026-05-07T10:21:05.823120",
    "finished_at":  "2026-05-07T10:21:08.998903",
    "elapsed_seconds":  3.16,
    "rto_remaining_seconds":  896.84,
    "rto_breached":  false,
    "snapshot_id":  null,
    "new_external_id":  null,
    "error":  "server 'ext-build-7' not found"
}
```

## 4. Inspect the audit trail

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/v1/vms/$vmId/audit" | ConvertTo-Json -Depth 10
```

```json
[
  {
    "id":  "2aafcbb60667477c99d71ba40dc69ad1",
    "vm_id":  "87b49905708e44e285080d09fb51e857",
    "job_id":  "1e9b6e12668649ef89df7a102ea36458",
    "action":  "dr.failed",
    "level":  "ERROR",
    "message":  "DR pipeline failed: server 'ext-build-7' not found",
    "from_state":  "snapshotting",
    "to_state":  "failed",
    "request_id":  "0789b4ca7b1a483d9693df4358dfd254",
    "payload":  {},
    "created_at":  "2026-05-07T10:21:09.030014"
  },
  {
    "id":  "94d4c691dc914e4fb693712e164d0517",
    "vm_id":  "87b49905708e44e285080d09fb51e857",
    "job_id":  "1e9b6e12668649ef89df7a102ea36458",
    "action":  "dr.snapshot",
    "level":  "INFO",
    "message":  "Creating Glance/Cinder snapshot",
    "from_state":  "failing",
    "to_state":  "snapshotting",
    "request_id":  "0789b4ca7b1a483d9693df4358dfd254",
    "payload":  {},
    "created_at":  "2026-05-07T10:21:05.904247"
  },
  {
    "id":  "6f25be5a2fe54e169794e3f9f1d9642b",
    "vm_id":  "87b49905708e44e285080d09fb51e857",
    "job_id":  "1e9b6e12668649ef89df7a102ea36458",
    "action":  "dr.start",
    "level":  "INFO",
    "message":  "DR pipeline initiated",
    "from_state":  "healthy",
    "to_state":  "failing",
    "request_id":  "0789b4ca7b1a483d9693df4358dfd254",
    "payload":  {},
    "created_at":  "2026-05-07T10:21:05.861665"
  },
  {
    "id":  "99c5936e28a848bc836bc9f60a26df00",
    "vm_id":  "87b49905708e44e285080d09fb51e857",
    "job_id":  null,
    "action":  "dr.triggered",
    "level":  "INFO",
    "message":  "DR pipeline triggered",
    "from_state":  null,
    "to_state":  null,
    "request_id":  "0789b4ca7b1a483d9693df4358dfd254",
    "payload":  { "rto_minutes":  15 },
    "created_at":  "2026-05-07T10:21:05.769254"
  },
  {
    "id":  "59a84223321d4ce181765ec01f983976",
    "vm_id":  "87b49905708e44e285080d09fb51e857",
    "job_id":  null,
    "action":  "vm.registered",
    "level":  "INFO",
    "message":  "Registered VM 'build-agent-7' for DR",
    "from_state":  null,
    "to_state":  "healthy",
    "request_id":  "b0d36f70d5d2485797506385ace2026a",
    "payload":  { "external_id":  "ext-build-7" },
    "created_at":  "2026-05-07T10:19:44.748029"
  }
]
```
