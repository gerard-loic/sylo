Lancer le serveur : uvicorn app.main:app --reload

Créer un rôle : ./cmd.sh create_role --name "Nom" --uid "Code" (--dry-run)

Créer un utilisateur : ./cmd.sh create_user --email a@b.fr --first_name A --last_name B (--password P) (--role-uid CODE) (--dry-run)

Associer des permissions : ./cmd.sh assign_permissions --role-uid ADMIN --config-file generate-config.json

Créer des permissions : ./cmd.sh sync_permissions --dry-run --config-file generate-config.json

Générer les entités CRUD depuis la base : ./cmd.sh generate_entities --config-file generate-config.json --dry-run

Générer les routes : 
./cmd.sh generate_entities --exclude-file FILE (--dry-run) (--force)

doc :
docs/
redoc/
openapi.json

Générer une collection Bruno (zip) : ./cmd.sh generate_bruno_collection (--output FILE) (--base-url URL)

MCP :
http://localhost:8000/mcp
make cmd c="sync_permissions --dry-run