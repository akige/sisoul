class Sisoul < Formula
  include Language::Python::Virtualenv

  desc "Decentralized P2P AI agent protocol (did:key + kubo IPFS + Signal chat)"
  homepage "https://github.com/akige/sisoul"
  url "https://github.com/akige/sisoul/archive/refs/tags/v1.0.0-alpha.tar.gz"
  sha256 "dbeeda0a5315fda26d573313d0b4efe7e9a8c55bbcd04f02fb043d31efd95650"
  license "Apache-2.0"
  head "https://github.com/akige/sisoul.git", branch: "main"

  depends_on "python@3.12"
  depends_on "kubo" => :recommended

  # NOTE: alpha pre-release uses live PyPI resolution (resources not vendored).
  # For the v1.0 stable release we will vendor every dependency as a `resource`
  # block per Homebrew style guide. The alpha intentionally lets pip resolve
  # at install time so we can iterate the dependency set without re-cutting
  # the Homebrew formula on every alpha release.

  def install
    virtualenv_create(libexec, "python3.12")
    # editable install with the four alpha feature groups, matching
    # `pip install -e '.[daemon,crypto,chat,llm]'` from docs/INSTALL.md.
    system libexec/"bin/pip", "install", "--quiet", "--upgrade", "pip"
    system libexec/"bin/pip", "install", "--quiet", ".[daemon,crypto,chat,llm]"
    bin.install_symlink libexec/"bin/sisoul"
  end

  def caveats
    <<~EOS
      sisoul is alpha v1.0.0-alpha — your AI agent, your data, no cloud.

      Quick start:
        sisoul init --goals "试试 sisoul"
        sisoul founder init --from #{opt_libexec}/share/sisoul/vault-template/founder
        sisoul daemon &
        sisoul self-check

      The kubo dependency is :recommended — without it the daemon's
      P2P swarm features are disabled but local CLI and vault operations
      still work. Install kubo later with `brew install kubo` if you skipped it.

      Docs: https://github.com/akige/sisoul/blob/main/docs/INSTALL.md
    EOS
  end

  test do
    # sisoul --version prints a banner containing the version string.
    assert_match(/1\.0/, shell_output("#{bin}/sisoul --version"))

    # `sisoul self-check` exits non-zero on a fresh install (no vault yet),
    # but the binary must run and print the check report. Accept any exit
    # status — we only care that the wrapper, venv, and module imports work.
    output = shell_output("#{bin}/sisoul self-check 2>&1", 1)
    assert_match(/sisoul self-check/, output)
  end
end
