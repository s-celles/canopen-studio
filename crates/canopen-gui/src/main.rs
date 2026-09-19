slint::include_modules!();

fn main() -> Result<(), slint::PlatformError> {
    let ui = MainWindow::new()?;

    // Setup an initial state
    ui.set_rpm(0);
    ui.set_nmt_state("Initializing...".into());

    ui.run()
}
