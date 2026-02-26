"""Custom CSS theme for the Shorts Engine dashboard."""

THEME_CSS = """
<style>
    /* Sidebar branding */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0e1117 0%, #1a1d24 100%);
    }
    [data-testid="stSidebar"] h1 {
        font-size: 1.3rem;
        letter-spacing: 0.5px;
    }

    /* Metric card styling */
    .metric-card {
        background: #1e2130;
        border: 1px solid #2d3250;
        border-radius: 12px;
        padding: 1.2rem;
        text-align: center;
        transition: border-color 0.2s;
    }
    .metric-card:hover {
        border-color: #4a69bd;
    }
    .metric-card .value {
        font-size: 2rem;
        font-weight: 700;
        color: #e2e8f0;
        line-height: 1.2;
    }
    .metric-card .label {
        font-size: 0.85rem;
        color: #94a3b8;
        margin-top: 0.3rem;
    }

    /* Status badges */
    .badge {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 12px;
        font-size: 0.78rem;
        font-weight: 600;
        letter-spacing: 0.3px;
    }
    .badge-success { background: #065f46; color: #6ee7b7; }
    .badge-warning { background: #78350f; color: #fbbf24; }
    .badge-danger  { background: #7f1d1d; color: #fca5a5; }
    .badge-info    { background: #1e3a5f; color: #7dd3fc; }
    .badge-neutral { background: #374151; color: #d1d5db; }

    /* Log viewer */
    .log-viewer {
        background: #0d1117;
        border: 1px solid #21262d;
        border-radius: 8px;
        padding: 1rem;
        font-family: 'Cascadia Code', 'Fira Code', monospace;
        font-size: 0.82rem;
        line-height: 1.5;
        max-height: 400px;
        overflow-y: auto;
        white-space: pre-wrap;
        word-break: break-word;
        color: #c9d1d9;
    }

    /* Section headers */
    .section-header {
        font-size: 1.1rem;
        font-weight: 600;
        color: #e2e8f0;
        padding-bottom: 0.5rem;
        border-bottom: 1px solid #2d3250;
        margin-bottom: 1rem;
    }

    /* Pipeline status indicator */
    .pipeline-status {
        display: flex;
        gap: 0.5rem;
        align-items: center;
        padding: 0.5rem 0;
    }
    .phase-dot {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        display: inline-block;
    }
    .phase-dot.success { background: #22c55e; }
    .phase-dot.running { background: #eab308; animation: pulse 1.5s infinite; }
    .phase-dot.failed  { background: #ef4444; }
    .phase-dot.idle    { background: #4b5563; }

    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.4; }
    }

    /* Action bar */
    .action-bar {
        display: flex;
        gap: 0.5rem;
        flex-wrap: wrap;
        padding: 0.75rem 0;
    }

    /* Compact tables */
    .stDataFrame { font-size: 0.85rem !important; }

    /* Toast-like notification area */
    .toast-area {
        position: fixed;
        top: 1rem;
        right: 1rem;
        z-index: 9999;
    }
</style>
"""


def inject_css():
    """Inject custom CSS into the Streamlit page."""
    import streamlit as st
    st.markdown(THEME_CSS, unsafe_allow_html=True)
