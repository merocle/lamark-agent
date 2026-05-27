use tokio::signal::unix;
use tokio::signal::unix::SignalKind;
use tracing::info;

/// Install signal handlers for graceful shutdown.
pub async fn install() -> Result<(), String> {
    // Set up SIGINT and SIGTERM handlers.
    let _sigint = unix::signal(SignalKind::interrupt()).map_err(|e| e.to_string())?;
    let _sigterm = unix::signal(SignalKind::terminate()).map_err(|e| e.to_string())?;

    tokio::spawn(async move {
        tokio::select! {
            _ = _sigint.recv() => info!("Received SIGINT"),
            _ = _sigterm.recv() => info!("Received SIGTERM"),
        }
    });

    Ok(())
}