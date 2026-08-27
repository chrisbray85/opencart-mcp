{
  lib,
  python3Packages,
  src,
}:

python3Packages.buildPythonPackage {
  pname = "opencart-mcp";
  version = "0.7.0";
  __structuredAttrs = true;

  inherit src;

  format = "pyproject";
  nativeBuildInputs = [ python3Packages.setuptools ];

  propagatedBuildInputs = with python3Packages; [
    fastmcp
    paramiko
    pymysql
    python-dotenv
  ];

  meta = {
    description = "MCP server for LiveStore / OpenCart 3 + Technics.";
    homepage = "https://github.com/Penikov/livestore-mcp";
    license = lib.licenses.mit;
    maintainers = [ lib.maintainers.icedborn ];
  };

  doCheck = false;
}
