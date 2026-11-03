"""DAG do Airflow para o pipeline de dados da Empório da Música (template).

As tasks e as dependências são **geradas** de `emporio.pipeline.airflow.task_graph`, isto é, das
mesmas etapas que `emporio pipeline run` executa localmente: não há lista mantida à mão. Cada task
roda uma etapa com `run_task` (incremental e com linhagem, como no pipeline local).

Uso: instale o projeto (`uv sync` / `pip install .`) no ambiente do Airflow, aponte as variáveis
de ambiente do projeto (`DATA_RAW_DIR`, `DATA_PROCESSED_DIR`, `EMBEDDING_BASE_URL`…) e copie
este arquivo para a pasta de DAGs. Compatível com Airflow 2.x e 3.x.

Em produção, a etapa `snapshot_raw` passaria a ler o ERP (ou uma réplica) em vez dos CSVs; o
agendamento diário abaixo é o ponto de partida (ver docs/architecture.md).
"""

from __future__ import annotations

from datetime import UTC, datetime

from airflow import DAG

try:  # Airflow 3
    from airflow.providers.standard.operators.python import PythonOperator
except ImportError:  # Airflow 2
    from airflow.operators.python import PythonOperator

from emporio.pipeline.airflow import run_task, task_graph

with DAG(
    dag_id="emporio_pipeline",
    description="CSVs + PDF → SQLite, políticas curadas e índice vetorial (mesmas etapas do CLI)",
    schedule="@daily",
    start_date=datetime(2026, 3, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,  # as etapas escrevem em data/processed: uma execução por vez
    tags=["emporio", "pipeline"],
) as dag:
    specs = task_graph()
    tasks = {
        t.task_id: PythonOperator(
            task_id=t.task_id,
            python_callable=run_task,
            op_kwargs={"stage": t.task_id},
            doc_md=t.doc,
            retries=1,
        )
        for t in specs
    }
    for t in specs:
        for upstream in t.upstream:
            tasks[upstream] >> tasks[t.task_id]
