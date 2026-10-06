# VNA application coverage contract

The repository tracks two different kinds of coverage. They answer different questions and must
not be combined into a single percentage:

- `vna_commands.v1.json` is the project-owned core command contract. Its reports show whether every
  command the project documented is registered. A result such as `393/393` does **not** mean every
  command used by every VNA application has been audited.
- `vna_application_compatibility.v1.json` is the application syntax inventory. It lists every
  advertised application and the command forms reviewed for that application. Its reports compare
  those forms with the live typed registry.

The application reports are:

- `reports/vna-application-coverage-vna-2-port.json`
- `reports/vna-application-coverage-vna-4-port.json`

At the current checkpoint, three applications are audited: gain compression (72 forms), active hot
parameters (23 forms), and noise figure (38 forms). All 133 inventoried forms are implemented on
both models. The other 22 advertised applications are marked `pending`; they are not represented
as complete simply because each has at least one option-gated command. CI verifies that completed
inventories have no missing commands and that the checked-in reports match the live registry.

The inventory contains normalized command syntax and project-written behavioral metadata only. It
does not reproduce vendor manual prose or model/option identifiers.
