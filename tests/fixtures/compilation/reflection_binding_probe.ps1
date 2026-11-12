$ErrorActionPreference = 'Stop'
$InputPath = $env:LOCALIZATION_SYNTHETIC_INPUT
$OutputPath = $env:LOCALIZATION_SYNTHETIC_OUTPUT
if (!$InputPath -or !$OutputPath) { throw 'Synthetic paths must be provided in process environment' }
$source = @'
using System.IO;
namespace SyntheticCompiler {
    public sealed class BGItoMG {
        public BGItoMG(string input, string output) {
            File.WriteAllText(output, File.ReadAllText(input));
        }
    }
}
'@
Add-Type -TypeDefinition $source
$assembly = [SyntheticCompiler.BGItoMG].Assembly
$type = $assembly.GetType('SyntheticCompiler.BGItoMG', $true)
$constructor = $type.GetConstructor([type[]]@([string], [string]))
if (!$constructor -or !$constructor.IsPublic) { throw 'Exact public string,string constructor was not found' }
$null = $constructor.Invoke([object[]]@([string]$InputPath, [string]$OutputPath))
if (!(Test-Path -LiteralPath $OutputPath -PathType Leaf)) { throw 'Synthetic output was not created' }
