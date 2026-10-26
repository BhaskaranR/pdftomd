// anyref <file> [ext]: print anydoc's markdown, exactly as Firecrawl's convert_document_to_markdown does.
fn main() {
    let args: Vec<String> = std::env::args().collect();
    let data = std::fs::read(&args[1]).expect("read");
    let format = anydoc::Format::from_bytes(&data)
        .or_else(|| args.get(2).and_then(|e| anydoc::Format::from_extension(e.trim_start_matches('.'))));
    match anydoc::to_markdown_bytes(&data, format) {
        Ok(md) => print!("{md}"),
        Err(e) => { eprintln!("ERROR {e}"); std::process::exit(2) }
    }
}
