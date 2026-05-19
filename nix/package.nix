{
  lib,
  python3Packages,
  src,
}:

python3Packages.buildPythonPackage {
  pname = "opencart-mcp";
  version = "0.4.1";
  __structuredAttrs = true;

  inherit src;

  format = "pyproject";
  nativeBuildInputs = [ python3Packages.setuptools ];

  propagatedBuildInputs = with python3Packages; [
    fastmcp
    paramiko
    python-dotenv
  ];

  meta = {
    description = "MCP server for OpenCart.";
    homepage = "https://github.com/chrisbray85/opencart-mcp";
    license = lib.licenses.mit;
    maintainers = [ lib.maintainers.icedborn ];
  };

  doCheck = false;
}
