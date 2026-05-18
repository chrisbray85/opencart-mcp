{
  description = "MCP server for OpenCart — query products, orders, settings, and modules";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems =
        f:
        nixpkgs.lib.genAttrs systems (
          system: f system nixpkgs.legacyPackages.${system}
        );
      mkPkg =
        pkgs:
        let
          opencart-mcp = pkgs.callPackage ./nix/package.nix { src = self; };
          pythonEnv = pkgs.python3.withPackages (ps: [ opencart-mcp ]);
        in
        pkgs.writeShellScriptBin "opencart-mcp" ''
          exec ${pythonEnv}/bin/python -m opencart_mcp.server "$@"
        '';
    in
    {
      packages = forAllSystems (
        system: pkgs: rec {
          opencart-mcp = mkPkg pkgs;
          default = opencart-mcp;
        }
      );

      apps = forAllSystems (
        system: pkgs: rec {
          opencart-mcp = {
            type = "app";
            program = "${self.packages.${system}.opencart-mcp}/bin/opencart-mcp";
          };
          default = opencart-mcp;
        }
      );

      devShells = forAllSystems (
        system: pkgs: {
          default = pkgs.mkShell {
            packages = [
              (pkgs.python3.withPackages (ps: [
                ps.fastmcp
                ps.paramiko
                ps.python-dotenv
              ]))
            ];
          };
        }
      );
    };
}
