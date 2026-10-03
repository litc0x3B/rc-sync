{ lib }:

let
  toNixType = rootSchema: isRequired: prop:
    let
      defs = rootSchema."$defs" or {};
      resolved = if prop ? "$ref" then
        let refKey = lib.last (lib.splitString "/" prop."$ref");
        in defs.${refKey}
      else prop;
      t = resolved.type or "string";
      hasDefault = resolved ? default;
    in
      if t == "string" then
        if isRequired || hasDefault then lib.types.str else lib.types.nullOr lib.types.str
      else if t == "integer" then
        if isRequired || hasDefault then lib.types.int else lib.types.nullOr lib.types.int
      else if t == "boolean" then
        if isRequired || hasDefault then lib.types.bool else lib.types.nullOr lib.types.bool
      else if t == "array" then
        lib.types.listOf (toNixType rootSchema true resolved.items)
      else if t == "object" then
        if (resolved ? additionalProperties) && (builtins.isAttrs resolved.additionalProperties) then
          lib.types.attrsOf (toNixType rootSchema false resolved.additionalProperties)
        else
          lib.types.submodule {
            options = schemaPropertiesToOptions rootSchema resolved;
          }
      else
        lib.types.anything;

  schemaPropertiesToOptions = rootSchema: objSchema:
    let
      requiredList = objSchema.required or [];
      defs = rootSchema."$defs" or {};
    in
      lib.mapAttrs (name: prop:
        let
          resolved = if prop ? "$ref" then
            let refKey = lib.last (lib.splitString "/" prop."$ref");
            in defs.${refKey}
          else prop;
          isRequired = lib.elem name requiredList;
          nixType = toNixType rootSchema isRequired prop;
          isAttrsOf = (resolved.type or "") == "object" && (resolved ? additionalProperties) && (builtins.isAttrs resolved.additionalProperties);
          isArray = (resolved.type or "") == "array";
          opt = {
            type = nixType;
          } // lib.optionalAttrs (prop ? description) {
            description = prop.description;
          } // lib.optionalAttrs (prop ? default) {
            default = prop.default;
          } // lib.optionalAttrs (! (prop ? default) && isArray) {
            default = [];
          } // lib.optionalAttrs (! (prop ? default) && isAttrsOf) {
            default = {};
          } // lib.optionalAttrs (! (prop ? default) && !isRequired && !isArray && !isAttrsOf) {
            default = null;
          };
        in
          lib.mkOption opt
      ) (objSchema.properties or {});
in
{
  inherit toNixType schemaPropertiesToOptions;

  schemaToSubmodule = schema: lib.types.submodule {
    options = schemaPropertiesToOptions schema schema;
  };
}
