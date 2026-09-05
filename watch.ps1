param(
    [string]$InputPath = ".\input",
    [string]$OutputPath = ".\output",
    [string]$ReviewPath = ".\review",
    [ValidateSet("smart", "grid")]
    [string]$Segmentation = "smart",
    [int]$Rows = 0,
    [int]$Columns = 0,
    [int]$ExpectedFrames = 0,
    [ValidateSet("rembg", "chroma", "auto")]
    [string]$Mode = "auto",
    [string]$Model = "isnet-anime",
    [ValidateSet("auto", "cuda", "cpu")]
    [string]$Provider = "auto"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$workflowPath = Join-Path $projectRoot "sprite_workflow.py"

function Get-ProjectPath([string]$PathValue) {
    if ([IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return Join-Path $projectRoot $PathValue
}

$resolvedInput = Get-ProjectPath $InputPath
$resolvedOutput = Get-ProjectPath $OutputPath
$resolvedReview = Get-ProjectPath $ReviewPath

foreach ($directory in @($resolvedInput, $resolvedOutput, $resolvedReview)) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

& $pythonPath $workflowPath $resolvedInput `
    --watch `
    --output $resolvedOutput `
    --review $resolvedReview `
    --segmentation $Segmentation `
    --rows $Rows `
    --columns $Columns `
    --expected-frames $ExpectedFrames `
    --mode $Mode `
    --model $Model `
    --provider $Provider

exit $LASTEXITCODE
