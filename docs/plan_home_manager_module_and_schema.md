# Implementation Plan: Home Manager Module with Schema-driven Options and Build-time Validation

## Goal Description
Реализовать полноценный модуль Home Manager для `rc-sync`, решающий три ключевые задачи:
1. **Преобразование `schema.json` в опции Nix** (`nix/schema-to-options.nix`):
   - Парсит JSON-схему на этапе чистой оценки Nix (`pure eval`).
   - Преобразует поля, типы, описания и дефолты в `lib.types.submodule` и `lib.mkOption`.
   - Обеспечивает полноценное автодополнение, документацию и проверку базовых типов для `nixd` / IDE.
2. **Генерация YAML и строгая валидация Pydantic на этапе сборки**:
   - Формирует YAML-конфиг из `cfg.settings`.
   - Вызывает `${cfg.package}/bin/rc-sync config validate` через `pkgs.runCommand` во время `home-manager switch`.
   - При ошибках (дубликат алиаса, `sync_freq < 3` и др.) сборка останавливается с понятным сообщением об ошибке от Pydantic.
3. **Генерация юнитов systemd через `rc-sync daemon print`**:
   - `rc-sync.service` генерируется через `${cfg.package}/bin/rc-sync daemon print service --exec-path "${cfg.package}/bin/rc-sync"`.
   - `rc-sync.timer` генерируется через `${cfg.package}/bin/rc-sync daemon print timer` с передачей сгенерированного валидного конфига.
   - Симлинкается `timers.target.wants/rc-sync.timer` для автоматического включения.
4. **Экспорт в `flake.nix`**:
   - `homeManagerModules.default` и `homeManagerModules.rc-sync`.

## User Review Required
> [!NOTE]
> **Расположение `schema.json` в репозитории:**  
> Чтобы Nix мог вычислять типы и подсказки для `nixd` в чистом режиме (без дорогого и нежелательного IFD — *Import From Derivation*), файл `schema.json` сохраняется в корне репозитория и читается через `builtins.fromJSON (builtins.readFile ./schema.json)`.  
> Мы добавим тест в pytest, гарантирующий, что `schema.json` в репозитории всегда на 100% синхронизирован с выводом `rc-sync config schema print`.

## Proposed Changes

### 1. `schema.json`
#### [NEW] `schema.json`
- Сгенерированный файл схемы Pydantic в корне репозитория (источник правды для чистого Nix eval).
- Автоматически синхронизируется и проверяется тестами.

---

### 2. Транслятор схемы в опции Nix: `nix/schema-to-options.nix`
#### [NEW] `nix/schema-to-options.nix`
Чистая рекурсивная функция на Nix без внешних зависимостей:
- Разрешает `$ref` (включая ссылки на `#/$defs/...`).
- Маппит типы JSON Schema на `lib.types`:
  - `string` -> `lib.types.str`
  - `integer` -> `lib.types.int`
  - `boolean` -> `lib.types.bool`
  - `array` -> `lib.types.listOf (тип items)`
  - `object` -> `lib.types.submodule { options = ...; }`
- Проставляет `description` и `default` из схемы.
- Предоставляет функцию `schemaToSubmodule schema`.

```nix
{ lib }:

let
  toNixType = rootSchema: prop:
    let
      defs = rootSchema."$defs" or {};
      resolved = if prop ? "$ref" then
        let refKey = lib.last (lib.splitString "/" prop."$ref");
        in defs.${refKey}
      else prop;
      t = resolved.type or "string";
    in
      if t == "string" then lib.types.str
      else if t == "integer" then lib.types.int
      else if t == "boolean" then lib.types.bool
      else if t == "array" then lib.types.listOf (toNixType rootSchema resolved.items)
      else if t == "object" then lib.types.submodule {
        options = toOptions rootSchema resolved;
      }
      else lib.types.anything;

  toOptions = rootSchema: objSchema:
    lib.mapAttrs (name: prop:
      lib.mkOption {
        description = prop.description or "";
        type = toNixType rootSchema prop;
        default = prop.default or (if (prop.type or "") == "array" then [] else null);
      }
    ) (objSchema.properties or {});
in
{
  schemaToSubmodule = schema: lib.types.submodule {
    options = toOptions schema schema;
  };
}
```

---

### 3. Модуль Home Manager: `nix/hm-module.nix`
#### [NEW] `nix/hm-module.nix`
Модуль для интеграции в Home Manager:
- Опция `services.rc-sync.enable` (`mkEnableOption`).
- Опция `services.rc-sync.package` (`mkPackageOption` / fallback на `self.packages`).
- Опция `services.rc-sync.settings` (типизирована через `schemaToSubmodule`).
- В секции `config`:
  1. `rawConfig = (pkgs.formats.yaml {}).generate "rc-sync-raw.yaml" cfg.settings;`
  2. `validatedConfig = pkgs.runCommand "rc-sync-config.yaml" {} '' ... '';` (валидация через `rc-sync config validate`).
  3. `serviceUnit` и `timerUnit` через `rc-sync daemon print service` и `rc-sync daemon print timer`.
  4. Симлинки в `xdg.configFile`:
     - `rc-sync/config.yaml`
     - `systemd/user/rc-sync.service`
     - `systemd/user/rc-sync.timer`
     - `systemd/user/timers.target.wants/rc-sync.timer`
  5. Добавление `cfg.package` в `home.packages`.

---

### 4. Экспорт модуля в `flake.nix`
#### [MODIFY] `flake.nix`
- Экспортировать `homeManagerModules.default = import ./nix/hm-module.nix self;`
- Экспортировать `homeManagerModules.rc-sync = self.homeManagerModules.default;`
- Добавить в `checks` сборку теста модуля Home Manager.

---

### 5. Тестирование и верификация
#### [NEW] `tests/test_nix_module.py` (или интеграция в `tests/test_config.py`)
- Тест актуальности `schema.json` в корне репозитория (проверка, что `schema.json` совпадает с `get_json_schema()`).
- Тест вычисления модуля Nix через `nix eval` / `nix build`:
  - Проверка корректного вычисления `options.services.rc-sync.settings`.
  - Проверка генерации `rc-sync-config.yaml`, `rc-sync.service`, `rc-sync.timer`.

---

## Verification Plan

### Automated Tests
1. `nix develop --command pytest` — запуск всех тестов Python.
2. Проверка оценки Nix модуля:
   ```bash
   nix eval .#homeManagerModules.default
   ```
3. Проверка сборки тестовой конфигурации:
   ```bash
   nix build --no-link .#packages.x86_64-linux.default
   ```
4. Проверка сборочной валидации:
   Собрать деривацию с заведомо валидным и невалидным конфигом и убедиться, что невалидный конфиг блокирует сборку с сообщением Pydantic.
