{
  description = "pr-swipe: human-gated PR triage";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      py = pkgs.python3;
      deps = ps: with ps; [ pyside6 jsonschema pyjwt cryptography pygments mcp ];
      pr-swipe = py.pkgs.buildPythonApplication {
        pname = "pr-swipe";
        version = "0.1.0";
        pyproject = true;
        src = ./.;
        build-system = [ py.pkgs.setuptools ];
        dependencies = deps py.pkgs;
        nativeCheckInputs = [ py.pkgs.pytestCheckHook py.pkgs.pytest-qt pkgs.git ];
        preCheck = "export QT_QPA_PLATFORM=offscreen HOME=$TMPDIR";
        # verify.py and gitview.py shell out to git at runtime.
        makeWrapperArgs = [ "--prefix" "PATH" ":" "${pkgs.git}/bin" ];
        postInstall = ''
          install -Dm644 pr_swipe/gui/icon.svg $out/share/icons/hicolor/scalable/apps/pr-swipe.svg
        '';
      };
    in {
      packages.${system}.default = pr-swipe;
      devShells.${system}.default = pkgs.mkShell {
        packages = [ (py.withPackages (ps: deps ps ++ [ ps.pytest ps.pytest-qt ])) pkgs.git ];
        QT_QPA_PLATFORM = "offscreen";
      };
    };
}
