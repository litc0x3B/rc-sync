self:
{ config, lib, pkgs, ... }:

let
  cfg = config.services.rc-sync;
  schemaToOptions = import ./schema-to-options.nix { inherit lib; };
  schema = builtins.fromJSON (builtins.readFile ../schema.json);
  settingsSubmodule = schemaToOptions.schemaToSubmodule schema;
in
{
  options.services.rc-sync = {
    enable = lib.mkEnableOption "rc-sync declarative bisync daemon";

    package = lib.mkOption {
      type = lib.types.package;
      default =
        if pkgs ? rc-sync
        then pkgs.rc-sync
        else self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      defaultText = lib.literalExpression "pkgs.rc-sync (or self.packages.\${system}.default)";
      description = "The rc-sync package to use.";
    };

    settings = lib.mkOption {
      type = settingsSubmodule;
      default = {};
      description = "Declarative configuration for rc-sync, mapped from schema.json.";
    };
  };

  config = lib.mkIf cfg.enable (let
    yamlFormat = pkgs.formats.yaml {};
    rawConfigFile = yamlFormat.generate "rc-sync-raw-config.yaml" cfg.settings;

    # 1. Build-time validation using rc-sync config validate:
    # If the user configuration violates schema or Pydantic rules (e.g. duplicate alias,
    # sync_freq_minutes < 3), the build will fail immediately with Pydantic's error message.
    validatedConfigFile = pkgs.runCommand "rc-sync-config.yaml" {} ''
      ${cfg.package}/bin/rc-sync config validate ${rawConfigFile}
      cp ${rawConfigFile} $out
    '';

    # 2. Service unit generated via rc-sync daemon print service:
    serviceUnit = pkgs.runCommand "rc-sync.service" {} ''
      export RC_SYNC_CONFIG_PATH="${validatedConfigFile}"
      ${cfg.package}/bin/rc-sync daemon print service --exec-path "${cfg.package}/bin/rc-sync" > $out
    '';

    # 3. Timer unit generated via rc-sync daemon print timer:
    timerUnit = pkgs.runCommand "rc-sync.timer" {} ''
      export RC_SYNC_CONFIG_PATH="${validatedConfigFile}"
      ${cfg.package}/bin/rc-sync daemon print timer > $out
    '';
  in {
    # Provide the CLI in user's profile
    home.packages = [ cfg.package ];

    # Link validated configuration file
    xdg.configFile."rc-sync/config.yaml".source = validatedConfigFile;

    # Link systemd user service and timer units
    xdg.configFile."systemd/user/rc-sync.service".source = serviceUnit;
    xdg.configFile."systemd/user/rc-sync.timer".source = timerUnit;

    # Enable the timer in user systemd session
    xdg.configFile."systemd/user/timers.target.wants/rc-sync.timer".source = timerUnit;
  });
}
