$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$SqlCsvDir = Join-Path $RepoDir "crawl_data\data\gold\sql"
$DataDir = Join-Path $ScriptDir "data"
$ContainerName = "cs2307-postgres"
$DbName = "postgres"
$DbUser = "postgres"

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

# Remove existing container if running/stopped
$existing = docker ps -a --filter "name=^/$ContainerName$" --format "{{.Names}}"
if ($existing -eq $ContainerName) {
    Write-Host "Stopping and removing existing container $ContainerName..."
    docker rm -f $ContainerName | Out-Null
}

Write-Host "Starting PostgreSQL container $ContainerName (local trust auth)..."
docker run -d `
    --name $ContainerName `
    -p 5432:5432 `
    -e POSTGRES_USER=$DbUser `
    -e POSTGRES_DB=$DbName `
    -e POSTGRES_HOST_AUTH_METHOD=trust `
    -v "${DataDir}:/var/lib/postgresql/data" `
    -v "${SqlCsvDir}:/import:ro" `
    postgres:16-alpine | Out-Null

Write-Host "Waiting for PostgreSQL to accept connections..."
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    docker exec $ContainerName pg_isready -U $DbUser -d $DbName 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
}

if (-not $ready) {
    throw "PostgreSQL did not become ready in time. Check: docker logs $ContainerName"
}

Write-Host "Creating tables..."
Get-Content (Join-Path $ScriptDir "create_tables.sql") -Raw | docker exec -i $ContainerName psql -v ON_ERROR_STOP=1 -U $DbUser -d $DbName

Write-Host "Importing Gold SQL CSVs from $SqlCsvDir..."
docker exec $ContainerName psql -v ON_ERROR_STOP=1 -U $DbUser -d $DbName -c "\copy listings FROM '/import/listings.csv' WITH (FORMAT csv, HEADER true, NULL '')"
docker exec $ContainerName psql -v ON_ERROR_STOP=1 -U $DbUser -d $DbName -c "\copy amenities FROM '/import/amenities.csv' WITH (FORMAT csv, HEADER true, NULL '')"
docker exec $ContainerName psql -v ON_ERROR_STOP=1 -U $DbUser -d $DbName -c "\copy listing_amenities FROM '/import/listing_amenities.csv' WITH (FORMAT csv, HEADER true, NULL '')"

Write-Host "Creating indexes..."
Get-Content (Join-Path $ScriptDir "create_indexes.sql") -Raw | docker exec -i $ContainerName psql -v ON_ERROR_STOP=1 -U $DbUser -d $DbName

Write-Host "PostgreSQL is ready on localhost:5432 (db: $DbName, user: $DbUser, trust auth)"
