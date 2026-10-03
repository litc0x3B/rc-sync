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
    typer
  ];


  makeWrapperArgs = [
    "--prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.rclone ]}"
  ];

  nativeBuildInputs = [
    pkgs.installShellFiles
  ];

  nativeCheckInputs = with pkgs.python3Packages; [
    pytestCheckHook
  ];

  postFixup = ''
    install -d $out/share/rc-sync $out/share/doc/rc-sync
    $out/bin/rc-sync config schema gen $out/share/rc-sync/schema.json
    cp $out/share/rc-sync/schema.json $out/share/doc/rc-sync/schema.json

    installShellCompletion --cmd rc-sync \
      --bash <(_RC_SYNC_COMPLETE=source_bash $out/bin/rc-sync) \
      --zsh <(_RC_SYNC_COMPLETE=source_zsh $out/bin/rc-sync | sed '/./,$!d') \
      --fish <(_RC_SYNC_COMPLETE=source_fish $out/bin/rc-sync)
  '';


  meta = with pkgs.lib; {
    description = "rclone bisync wrapper for declarative synchronization and daemon management";
    mainProgram = "rc-sync";
  };
}
