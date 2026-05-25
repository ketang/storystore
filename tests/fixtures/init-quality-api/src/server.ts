import express from "express";

const app = express();

app.get("/api/health", (req, res) => {
  res.json({ status: "ok" });
});

app.post("/api/users", (req, res) => {
  res.status(201).json({ id: 1, name: req.body.name });
});

app.get("/api/users/:id", (req, res) => {
  res.json({ id: req.params.id, name: "Alice" });
});

app.put("/api/users/:id", (req, res) => {
  res.json({ id: req.params.id, updated: true });
});

app.delete("/api/users/:id", (req, res) => {
  res.status(204).send();
});

app.get("/api/widgets", (req, res) => {
  res.json({ widgets: [] });
});

app.post("/api/widgets", (req, res) => {
  res.status(201).json({ id: 1, name: req.body.name });
});

app.get("/api/reports", (req, res) => {
  res.json({ reports: [] });
});

app.listen(3000);
