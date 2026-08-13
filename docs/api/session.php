<?php
header('Content-Type: application/json; charset=utf-8');
if (session_status() !== PHP_SESSION_ACTIVE) {
    session_start();
}
echo json_encode(['authed' => !empty($_SESSION['profit_authed'])], JSON_UNESCAPED_UNICODE);
