use tokio::signal::unix;
use tokio::signal::unix::SignalKind;

/// Install signal handlers for graceful shutdown.
pub async fn install() -> Result<(), String> {
    let mut sigint = unix::signal(SignalKind::interrupt()).map_err(|e| e.to_string())?;
    let mut sigterm = unix::signal(SignalKind::terminate()).map_err(|e| e.to_string())?;

    tokio::spawn(async move {
        tokio::select! {
            _ = sigint.recv() => tracing::info!("Received SIGINT"),
            _ = sigterm.recv() => tracing::info!("Received SIGTERM"),
        }
    });

    Ok(())
}
