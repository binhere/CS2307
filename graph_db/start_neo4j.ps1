$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Split-Path -Parent $ScriptDir
$GraphCsvDir = Join-Path $RepoDir "crawl_data\data\gold\graph"
$DataDir = Join-Path $ScriptDir "data"
$ContainerName = "cs2307-neo4j"
$Neo4jAuth = "none"

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

# Remove existing container if running/stopped
$existing = docker ps -a --filter "name=^/$ContainerName$" --format "{{.Names}}"
if ($existing -eq $ContainerName) {
    Write-Host "Stopping and removing existing container $ContainerName..."
    docker rm -f $ContainerName | Out-Null
}

Write-Host "Running neo4j-admin bulk import from $GraphCsvDir..."
docker run --rm `
    -v "${DataDir}:/data" `
    -v "${GraphCsvDir}:/import:ro" `
    neo4j:5-community `
    neo4j-admin database import full neo4j `
    --overwrite-destination=true `
    --multiline-fields=true `
    --nodes=/import/graph_nodes_listing.csv `
    --nodes=/import/graph_nodes_amenity.csv `
    --nodes=/import/graph_nodes_geo_cluster.csv `
    --nodes=/import/graph_nodes_ward.csv `
    --nodes=/import/graph_nodes_former_admin_area.csv `
    --nodes=/import/graph_nodes_street.csv `
    --relationships=/import/graph_edges_listing_near_amenity.csv `
    --relationships=/import/graph_edges_listing_in_cluster.csv `
    --relationships=/import/graph_edges_listing_in_ward.csv `
    --relationships=/import/graph_edges_listing_in_former_area.csv `
    --relationships=/import/graph_edges_ward_mapped_from_former_area.csv `
    --relationships=/import/graph_edges_listing_on_street.csv

Write-Host "Starting Neo4j container $ContainerName..."
docker run -d `
    --name $ContainerName `
    -p 7474:7474 `
    -p 7687:7687 `
    -e NEO4J_AUTH=$Neo4jAuth `
    -v "${DataDir}:/data" `
    neo4j:5-community | Out-Null

Write-Host "Waiting for Neo4j to accept Cypher queries..."
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
        $res = docker exec $ContainerName cypher-shell "RETURN 1;" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
    } catch {}
}

if (-not $ready) {
    throw "Neo4j did not become ready in time. Check: docker logs $ContainerName"
}

Write-Host "Applying constraints and indexes..."
Get-Content (Join-Path $ScriptDir "create_indexes.cypher") -Raw | docker exec -i $ContainerName cypher-shell

Write-Host "Neo4j is ready at http://localhost:7474 (auth disabled)"
