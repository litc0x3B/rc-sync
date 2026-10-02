{
  description = "rclone bisync wrapper for declarative synchronization and daemon management";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f:
        nixpkgs.lib.genAttrs supportedSystems (system:
          f nixpkgs.legacyPackages.${system} system
        );
    in
    {
      packages = forAllSystems (pkgs: system: {
        default = pkgs.callPackage ./default.nix {};
        rc-sync = pkgs.callPackage ./default.nix {};
      });

      apps = forAllSystems (pkgs: system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/rc-sync";
        };
      });

      devShells = forAllSystems (pkgs: system: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: with ps; [
              pydantic
              pyyaml
              platformdirs
              pytest
              mypy
              types-pyyaml
              ruff
              setuptools
            ]))
            pkgs.rclone
          ];
        };
      });

      overlays.default = final: prev: {
        rc-sync = final.callPackage ./default.nix {};
      };
    };
}
