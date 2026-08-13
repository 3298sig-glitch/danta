<?php
require_once __DIR__ . '/config.php';
header('Content-Type: application/json; charset=utf-8');

if (session_status() !== PHP_SESSION_ACTIVE) {
    session_start();
}

$input = json_decode(file_get_contents('php://input'), true) ?? [];
$pin = (string)($input['pin'] ?? '');

if ($pin !== '' && hash_equals((string)PROFIT_PIN, $pin)) {
    $_SESSION['profit_authed'] = true;
    echo json_encode(['ok' => true], JSON_UNESCAPED_UNICODE);
} else {
    http_response_code(401);
    echo json_encode(['ok' => false, 'error' => 'PIN이 올바르지 않습니다.'], JSON_UNESCAPED_UNICODE);
}
