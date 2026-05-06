// demo.rs — fixture for doc_comment extraction tests

/// Computes the sum of two integers.
/// Returns the result immediately.
fn add(a: i32, b: i32) -> i32 {
    // @Add integers, ADD_FUNC, impl, [REQ_ADD]
    a + b
}

fn multiply(a: i32, b: i32) -> i32 {
    // @Multiply integers, MUL_FUNC, impl, [REQ_MUL]
    a * b
}

/// A simple point struct.
struct Point {
    // @Point struct, POINT_STRUCT, impl, []
    x: f64,
    y: f64,
}
