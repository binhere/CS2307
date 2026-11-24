$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$ImagesDir = Join-Path $RepoDir "data\silver\images_scaled"
$DataDir = Join-Path $ScriptDir "data"
$ContainerName = "cs2307-minio"
$RootUser = "minioadmin"
$RootPass = "minioadmin"
$BucketName = "real-estate-images"

if (-not (Test-Path $ImagesDir)) {
    throw "Images directory not found: $ImagesDir"
}

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

# Remove existing container if running/stopped
$existing = docker ps -a --filter "name=^/$ContainerName$" --format "{{.Names}}"
if ($existing -eq $ContainerName) {
    Write-Host "Stopping and removing existing container $ContainerName..."
    docker rm -f $ContainerName | Out-Null
}

Write-Host "Starting MinIO container $ContainerName..."
docker run -d `
    --name $ContainerName `
    -p 9000:9000 `
    -p 9001:9001 `
    -e MINIO_ROOT_USER=$RootUser `
    -e MINIO_ROOT_PASSWORD=$RootPass `
    -v "${DataDir}:/data" `
    cgr.dev/chainguard/minio:latest server /data --console-address ":9001" | Out-Null

Write-Host "Waiting for MinIO health check..."
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:9000/minio/health/ready" -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {}
}

if (-not $ready) {
    throw "MinIO did not become ready in time. Check: docker logs $ContainerName"
}

Write-Host "Creating bucket '$BucketName' and syncing images from $ImagesDir..."
$McHost = "http://${RootUser}:${RootPass}@127.0.0.1:9000"
docker run --rm `
    --network "container:$ContainerName" `
    -e "MC_HOST_local=$McHost" `
    cgr.dev/chainguard/minio-client:latest `
    mb --ignore-existing "local/$BucketName"

docker run --rm `
    --network "container:$ContainerName" `
    -e "MC_HOST_local=$McHost" `
    -v "${ImagesDir}:/images:ro" `
    cgr.dev/chainguard/minio-client:latest `
    mirror /images "local/$BucketName/images"

Write-Host "MinIO is ready!"
Write-Host "  Console: http://localhost:9001 (user: $RootUser, pass: $RootPass)"
Write-Host "  S3 API:  http://localhost:9000 (bucket: $BucketName)"
