{ pkgs ? import <nixpkgs> {} }:

pkgs.python3Packages.buildPythonApplication {
  pname = "rc-sync";
  version = "0.1.0";
  pyproject = true;

  src = ./.;

  build-system = with pkgs.python3Packages; [
    setuptools
  ];

  dependencies = with pkgs.python3Packages; [
    pydantic
    pyyaml
    platformdirs
  ];

  makeWrapperArgs = [
    "--prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.rclone ]}"
  ];

  nativeCheckInputs = with pkgs.python3Packages; [
    pytestCheckHook
  ];

  postFixup = ''
    install -d $out/share/rc-sync
    $out/bin/rc-sync config schema gen $out/share/rc-sync/schema.json
  '';

  meta = with pkgs.lib; {
    description = "rclone bisync wrapper for declarative synchronization and daemon management";
    mainProgram = "rc-sync";
  };
}
