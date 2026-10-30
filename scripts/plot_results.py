#!/usr/bin/env python3
"""Gera os gráficos e a tabela-resumo do estudo de caso a partir do CSV
produzido por `test_cases.py --full --csv results/results.csv`.

Gera:
  results/tempo_download.png
      Tempo médio de download de um leecher por tamanho de arquivo, com uma
      curva por combinação de quantidade de peers e tamanho de bloco.

  results/fontes_blocos.png
      Nos cenários com 4 peers, a fração dos blocos que os leechers receberam
      de outros leechers em vez do seeder.

  results/resumo.csv
      Média, desvio padrão e vazão de cada cenário (usado no relatório).

Uso:
    python scripts/plot_results.py
"""

import csv
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # não precisa de display para salvar PNG
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
CSV_PATH = RESULTS_DIR / "results.csv"

SIZE_LABELS = {
    10 * 1024: "10 KB", 20 * 1024: "20 KB",
    1024 * 1024: "1 MB", 5 * 1024 * 1024: "5 MB",
    10 * 1024 * 1024: "10 MB", 20 * 1024 * 1024: "20 MB",
}


def load_results():
    """Lê results.csv e devolve as linhas com os campos numéricos convertidos."""
    if not CSV_PATH.exists():
        raise SystemExit(
            f"erro: {CSV_PATH} não encontrado. Rode "
            "'python test_cases.py --full --repeats 5 --csv results/results.csv' primeiro."
        )
    rows = []
    with open(CSV_PATH, newline="") as f:
        for row in csv.DictReader(f):
            sources = {}
            for item in filter(None, row["sources"].split(";")):
                name, _, count = item.partition("=")
                sources[name] = int(count)
            rows.append({
                "peers": int(row["peers"]),
                "block_size": int(row["block_size"]),
                "file_size": int(row["file_size"]),
                "peer": row["peer"],
                "ok": row["ok"] == "1",
                "elapsed_s": float(row["elapsed_s"]) if row["elapsed_s"] else None,
                "sources": sources,
            })
    return rows


def summarize(rows):
    """Agrupa por cenário e calcula média, desvio e vazão do tempo de cada leecher."""
    groups = defaultdict(list)
    for row in rows:
        groups[(row["peers"], row["block_size"], row["file_size"])].append(row)

    summary = []
    for (peers, block, size), items in sorted(groups.items()):
        times = [r["elapsed_s"] for r in items if r["elapsed_s"] is not None]
        mean = statistics.mean(times)
        from_seeder = sum(r["sources"].get("peer0", 0) for r in items)
        total = sum(sum(r["sources"].values()) for r in items)
        summary.append({
            "peers": peers,
            "block_size": block,
            "file_size": size,
            "blocks": -(-size // block),
            "ok": f"{sum(r['ok'] for r in items)}/{len(items)}",
            "mean_s": round(mean, 3),
            "stdev_s": round(statistics.stdev(times), 3) if len(times) > 1 else 0.0,
            "throughput_mb_s": round(size / (1024 * 1024) / mean, 2),
            "from_leechers_pct": round(100 * (total - from_seeder) / total, 1) if total else 0.0,
        })
    return summary


def write_summary(summary):
    out_path = RESULTS_DIR / "resumo.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    print(f"resumo salvo em {out_path}")


def plot_download_time(summary):
    """Um gráfico só: eixo X = tamanho do arquivo, uma curva por (peers, bloco)."""
    sizes = sorted({s["file_size"] for s in summary})
    x = range(len(sizes))

    plt.figure(figsize=(9, 5.5))
    for peers in (2, 4):
        for block in (1024, 4096):
            means = []
            for size in sizes:
                match = [s for s in summary if (s["peers"], s["block_size"], s["file_size"]) == (peers, block, size)]
                means.append(match[0]["mean_s"] if match else float("nan"))
            style = "-" if block == 1024 else "--"
            plt.plot(x, means, style, marker="o", label=f"{peers} peers, bloco {block // 1024} KB")

    plt.yscale("log")
    plt.xticks(list(x), [SIZE_LABELS.get(s, str(s)) for s in sizes])
    plt.xlabel("Tamanho do arquivo")
    plt.ylabel("Tempo médio de download por leecher (s, escala log)")
    plt.title("Tempo de download por tamanho de arquivo, quantidade de peers e tamanho de bloco")
    plt.legend()
    plt.grid(True, which="both", alpha=0.3)
    plt.tight_layout()

    out_path = RESULTS_DIR / "tempo_download.png"
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"gráfico salvo em {out_path}")


def plot_sources(summary):
    """Barras: fração dos blocos que os leechers receberam de outros leechers (4 peers)."""
    sizes = sorted({s["file_size"] for s in summary})
    width = 0.38

    plt.figure(figsize=(9, 4.5))
    for offset, block in ((-width / 2, 1024), (width / 2, 4096)):
        shares = []
        for size in sizes:
            match = [s for s in summary if (s["peers"], s["block_size"], s["file_size"]) == (4, block, size)]
            shares.append(match[0]["from_leechers_pct"] if match else 0.0)
        bars = plt.bar([i + offset for i in range(len(sizes))], shares, width, label=f"bloco {block // 1024} KB")
        plt.bar_label(bars, fmt="%.1f%%", fontsize=8)

    plt.xticks(range(len(sizes)), [SIZE_LABELS.get(s, str(s)) for s in sizes])
    plt.xlabel("Tamanho do arquivo")
    plt.ylabel("Blocos vindos de outros leechers (%)")
    plt.title("4 peers: parcela dos blocos que não veio do seeder")
    plt.legend()
    plt.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()

    out_path = RESULTS_DIR / "fontes_blocos.png"
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"gráfico salvo em {out_path}")


def main():
    rows = load_results()
    summary = summarize(rows)
    write_summary(summary)
    plot_download_time(summary)
    plot_sources(summary)


if __name__ == "__main__":
    main()
