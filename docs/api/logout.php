<?php
header('Content-Type: application/json; charset=utf-8');
if (session_status() !== PHP_SESSION_ACTIVE) {
    session_start();
}
$_SESSION = [];
session_destroy();
echo json_encode(['ok' => true], JSON_UNESCAPED_UNICODE);
